"""Fault-injection tests for the /query pipeline.

These mock the LLM boundary (backend.llm.generate_sql / summarize_result)
instead of hitting the real API. That's a deliberate choice: the failure
modes we care about here -- the LLM erroring, hallucinating a column,
returning multi-statement SQL, trying to DROP a table -- are about how
*our* code reacts, not about Gemini's actual behavior, and mocking makes
these deterministic and runnable with zero API quota or network dependency.
(The eval harness in backend/eval/ is the complementary piece that tests
against the real model to measure generation quality.)

Run: .venv/bin/pytest tests/ -v
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.db import QueryExecutionResult
from backend.llm import LLMError

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_state():
    """Each test gets a clean stats/conversation slate so assertions on
    counters don't depend on test execution order."""
    main._stats.update(
        {
            "requests_total": 0,
            "llm_errors": 0,
            "validation_failures": 0,
            "execution_errors": 0,
            "successes": 0,
        }
    )
    main._conversations.clear()
    yield


def test_llm_generation_failure_returns_graceful_error():
    with patch.object(main.llm, "generate_sql", side_effect=LLMError("boom")):
        resp = client.post("/query", json={"question": "anything"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "temporarily unavailable" in body["error"]
    assert main._stats["llm_errors"] == 1


def test_destructive_sql_is_blocked_before_execution():
    with patch.object(
        main.llm, "generate_sql", return_value="DROP TABLE financial_metrics"
    ):
        resp = client.post("/query", json={"question": "delete everything"})

    body = resp.json()
    assert body["status"] == "error"
    assert "safety validation" in body["error"]
    assert main._stats["validation_failures"] == 1

    # Prove the table actually still exists -- the block wasn't just a
    # response-shape check, the DB was genuinely never touched.
    from backend import db

    result = db.execute_safe_sql("SELECT COUNT(*) FROM financial_metrics")
    assert result.ok and result.rows[0][0] == 200


def test_statement_stacking_injection_is_blocked():
    with patch.object(
        main.llm,
        "generate_sql",
        return_value="SELECT * FROM regions; DROP TABLE regions;",
    ):
        resp = client.post("/query", json={"question": "sneaky"})

    body = resp.json()
    assert body["status"] == "error"
    assert main._stats["validation_failures"] == 1


def test_hallucinated_column_is_blocked():
    with patch.object(
        main.llm, "generate_sql", return_value="SELECT fake_column FROM financial_metrics"
    ):
        resp = client.post("/query", json={"question": "what is fake_column"})

    body = resp.json()
    assert body["status"] == "error"
    assert "Unknown column" in body["error"]


def test_execution_error_is_surfaced_not_crashed():
    with (
        patch.object(
            main.llm, "generate_sql", return_value="SELECT revenue FROM financial_metrics"
        ),
        patch.object(
            main.db,
            "execute_safe_sql",
            return_value=QueryExecutionResult(ok=False, error="disk I/O error"),
        ),
    ):
        resp = client.post("/query", json={"question": "revenue"})

    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] == "disk I/O error"
    assert main._stats["execution_errors"] == 1


def test_summarization_failure_degrades_gracefully_to_raw_rows():
    """If Gemini generates valid SQL but the second (summarization) call
    fails, the user should still get their data back -- just without the
    natural-language gloss. This was a deliberate degrade-don't-fail design
    choice (Q11/Q16: don't let a non-critical dependency take down a
    working result)."""
    with (
        patch.object(
            main.llm,
            "generate_sql",
            return_value="SELECT region_name FROM regions",
        ),
        patch.object(main.llm, "summarize_result", side_effect=LLMError("summary boom")),
    ):
        resp = client.post("/query", json={"question": "list regions"})

    body = resp.json()
    assert body["status"] == "ok"
    assert body["answer"] is None
    assert body["rows"]  # raw rows still returned
    assert main._stats["successes"] == 1
    assert main._stats["llm_errors"] == 1


def test_no_query_returns_clarification_not_a_guess():
    with patch.object(
        main.llm,
        "generate_sql",
        return_value="NO_QUERY: the schema has no employee-level data",
    ):
        resp = client.post("/query", json={"question": "list all employees"})

    body = resp.json()
    assert body["status"] == "clarification_needed"
    assert "employee-level data" in body["answer"]


def test_happy_path_end_to_end_with_conversation_memory():
    with (
        patch.object(
            main.llm,
            "generate_sql",
            return_value="SELECT region_name FROM regions ORDER BY region_name",
        ),
        patch.object(main.llm, "summarize_result", return_value="There are four regions."),
    ):
        resp = client.post("/query", json={"question": "list regions"})

    body = resp.json()
    assert body["status"] == "ok"
    assert body["answer"] == "There are four regions."
    assert len(body["rows"]) == 4
    assert main._stats["successes"] == 1

    conv_id = body["conversation_id"]
    assert main._conversations[conv_id]  # follow-up turns would see this history
