"""FastAPI backend for the text-to-SQL copilot.

Request flow for POST /query:
  1. Call Claude to turn the NL question (+ conversation history) into SQL.
  2. Validate the SQL (single SELECT, allowlisted tables/columns, forced LIMIT).
  3. Execute it against the read-only SQLite connection.
  4. Call Claude again to summarize the result rows in plain language.
  5. Log the whole request (question, generated SQL, validation outcome,
     latency, errors) and update in-memory counters exposed at /stats.
"""
import logging
import time
import uuid
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from . import db, llm
from .validator import validate_sql

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("texttosql")

app = FastAPI(title="Text-to-SQL Copilot")

# In-memory conversation store and stats counters. Fine for a single-process
# demo; a real deployment would move this to Redis/a DB so it survives
# restarts and works across multiple workers.
_conversations: dict[str, list[dict]] = {}
_stats = {
    "requests_total": 0,
    "llm_errors": 0,
    "validation_failures": 0,
    "execution_errors": 0,
    "successes": 0,
}


class QueryRequest(BaseModel):
    question: str
    conversation_id: str | None = None


class QueryResponse(BaseModel):
    conversation_id: str
    status: Literal["ok", "clarification_needed", "error"]
    answer: str | None = None
    sql: str | None = None
    columns: list[str] | None = None
    rows: list[list] | None = None
    error: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    return _stats


@app.get("/metrics")
def metrics():
    """Backs the dashboard charts: the full financial_metrics fact table
    joined with its dimensions. Small dataset (200 rows) -- filtering and
    aggregation for charts happens client-side in the Streamlit app rather
    than adding a combinatorial set of filtered backend endpoints."""
    result = db.fetch_dashboard_metrics()
    if not result.ok:
        return {"ok": False, "error": result.error}
    return {
        "ok": True,
        "columns": result.columns,
        "rows": [list(r) for r in result.rows],
    }


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    conversation_id = req.conversation_id or str(uuid.uuid4())
    history = _conversations.setdefault(conversation_id, [])

    _stats["requests_total"] += 1
    start = time.monotonic()
    log_ctx = {"conversation_id": conversation_id, "question": req.question}

    try:
        raw_sql = llm.generate_sql(req.question, history)
    except llm.LLMError as e:
        _stats["llm_errors"] += 1
        logger.error("llm_generation_failed %s error=%s", log_ctx, e)
        return QueryResponse(
            conversation_id=conversation_id,
            status="error",
            error="The assistant is temporarily unavailable. Please try again.",
        )

    if raw_sql.startswith("NO_QUERY:"):
        reason = raw_sql[len("NO_QUERY:") :].strip()
        logger.info("no_query %s reason=%s", log_ctx, reason)
        return QueryResponse(
            conversation_id=conversation_id,
            status="clarification_needed",
            answer=reason,
        )

    validation = validate_sql(raw_sql)
    if not validation.ok:
        _stats["validation_failures"] += 1
        logger.warning(
            "validation_failed %s raw_sql=%r reason=%s", log_ctx, raw_sql, validation.reason
        )
        return QueryResponse(
            conversation_id=conversation_id,
            status="error",
            sql=raw_sql,
            error=f"Generated query failed safety validation: {validation.reason}",
        )

    result = db.execute_safe_sql(validation.safe_sql)
    if not result.ok:
        _stats["execution_errors"] += 1
        logger.error(
            "execution_failed %s sql=%r error=%s", log_ctx, validation.safe_sql, result.error
        )
        return QueryResponse(
            conversation_id=conversation_id,
            status="error",
            sql=validation.safe_sql,
            error=result.error,
        )

    try:
        answer = llm.summarize_result(req.question, result.columns, result.rows)
    except llm.LLMError as e:
        _stats["llm_errors"] += 1
        logger.error("summarization_failed %s error=%s", log_ctx, e)
        answer = None  # degrade gracefully: still return raw rows

    history.append({"role": "user", "content": req.question})
    history.append({"role": "assistant", "content": validation.safe_sql})

    _stats["successes"] += 1
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "query_ok %s sql=%r rows=%d elapsed_ms=%.1f",
        log_ctx,
        validation.safe_sql,
        len(result.rows),
        elapsed_ms,
    )

    return QueryResponse(
        conversation_id=conversation_id,
        status="ok",
        answer=answer,
        sql=validation.safe_sql,
        columns=result.columns,
        rows=[list(r) for r in result.rows],
    )
