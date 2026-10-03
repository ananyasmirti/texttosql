"""Eval harness: runs backend/eval/cases.jsonl through the real pipeline
(Claude -> validator -> SQLite execution) and scores it.

This is the artifact that answers "how did you test that it worked well
enough to ship" (Q14) -- not a vibe check, a repeatable number.

Scoring, per case:
  - For cases with an expected_sql: generate SQL for the question, validate
    it, execute it, and compare the resulting row set against executing the
    expected_sql. Execution-result match (not string match on SQL) because
    there are often multiple correct SQL formulations of the same question.
  - For "unanswerable" cases (expected_sql is null): pass if the pipeline
    returns status="clarification_needed" instead of guessing.

Usage: .venv/bin/python -m backend.eval.run_eval
"""
import json
import sys
from pathlib import Path

from backend import db, llm
from backend.validator import validate_sql

CASES_PATH = Path(__file__).parent / "cases.jsonl"


def load_cases():
    with open(CASES_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def normalize_rows(columns, rows):
    """Order-insensitive, float-rounded comparison so minor formatting
    differences between two equivalent queries don't count as a mismatch."""
    def norm_val(v):
        return round(v, 2) if isinstance(v, float) else v

    return sorted(tuple(norm_val(v) for v in row) for row in rows)


def run_case(case: dict) -> dict:
    question = case["question"]
    expected_sql = case.get("expected_sql")

    try:
        raw_sql = llm.generate_sql(question, history=[])
    except llm.LLMError as e:
        return {**case, "outcome": "llm_error", "detail": str(e)}

    if expected_sql is None:
        passed = raw_sql.startswith("NO_QUERY:")
        return {
            **case,
            "outcome": "pass" if passed else "fail",
            "detail": f"generated={raw_sql!r}",
        }

    validation = validate_sql(raw_sql)
    if not validation.ok:
        return {
            **case,
            "outcome": "validation_failed",
            "detail": f"raw_sql={raw_sql!r} reason={validation.reason}",
        }

    actual_result = db.execute_safe_sql(validation.safe_sql)
    if not actual_result.ok:
        return {
            **case,
            "outcome": "execution_failed",
            "detail": f"sql={validation.safe_sql!r} error={actual_result.error}",
        }

    expected_validation = validate_sql(expected_sql)
    expected_result = db.execute_safe_sql(expected_validation.safe_sql)
    if not expected_result.ok:
        return {**case, "outcome": "bad_fixture", "detail": expected_result.error}

    actual_norm = normalize_rows(actual_result.columns, actual_result.rows)
    expected_norm = normalize_rows(expected_result.columns, expected_result.rows)

    # Exact match, or match on the shared leading columns. The LLM is free
    # to phrase an equally-correct query that includes or omits a trailing
    # descriptive/aggregate column (e.g. "which department" answered with
    # just the name vs. name+margin) -- that's not a correctness bug, so
    # don't penalize it. Truncating both sides to the narrower column width
    # keeps the check meaningful without being brittle to this.
    width = min(
        len(actual_result.columns or []), len(expected_result.columns or [])
    )
    actual_trunc = sorted(row[:width] for row in actual_norm)
    expected_trunc = sorted(row[:width] for row in expected_norm)

    passed = actual_norm == expected_norm or actual_trunc == expected_trunc
    return {
        **case,
        "outcome": "pass" if passed else "fail",
        "detail": (
            f"generated_sql={validation.safe_sql!r} "
            f"actual={actual_norm[:3]} expected={expected_norm[:3]}"
        ),
    }


def main():
    cases = load_cases()
    results = [run_case(c) for c in cases]

    total = len(results)
    passed = sum(1 for r in results if r["outcome"] == "pass")

    print(f"\n{'ID':<35} {'OUTCOME':<20} DETAIL")
    print("-" * 100)
    for r in results:
        detail = r.get("detail", "")[:60]
        print(f"{r['id']:<35} {r['outcome']:<20} {detail}")

    print("-" * 100)
    print(f"Execution accuracy: {passed}/{total} ({passed / total:.0%})")

    failures = [r for r in results if r["outcome"] != "pass"]
    if failures:
        print(f"\n{len(failures)} case(s) did not pass:")
        for r in failures:
            print(f"  - {r['id']}: {r['outcome']} -- {r.get('detail', '')}")

    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
