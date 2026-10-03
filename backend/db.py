"""Read-only query execution against the SQLite finance warehouse.

SQLite has no user/role system like Postgres, so "read-only" is enforced in
application code instead of a DB grant: open the file with the `?mode=ro`
URI flag (the OS-level file handle itself refuses writes) and additionally
rely on the validator already having guaranteed the statement is a single
SELECT. Belt and suspenders -- if the validator ever had a bug that let a
write through, the read-only connection would still reject it.
"""
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "finance.db"
QUERY_TIMEOUT_SECONDS = 5.0


@dataclass
class QueryExecutionResult:
    ok: bool
    columns: list[str] | None = None
    rows: list[tuple] | None = None
    elapsed_ms: float | None = None
    error: str | None = None


def _get_readonly_connection() -> sqlite3.Connection:
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=QUERY_TIMEOUT_SECONDS)
    # Belt-and-suspenders: progress_handler lets us abort a runaway query
    # even though our dataset is small enough this should never trigger.
    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS

    def _check_deadline():
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(_check_deadline, 1000)
    return conn


DASHBOARD_METRICS_SQL = """
SELECT
    q.label AS quarter_label, q.fiscal_year, q.quarter_num,
    r.region_name, d.department_name,
    f.revenue, f.cost_of_goods_sold, f.gross_profit, f.operating_expenses,
    f.operating_income, f.net_income, f.headcount, f.marketing_spend,
    f.rd_spend, f.capex, f.cash_balance, f.accounts_receivable,
    f.accounts_payable, f.inventory, f.customer_count, f.new_customers,
    f.churned_customers, f.avg_deal_size, f.gross_margin_pct,
    f.operating_margin_pct
FROM financial_metrics f
JOIN quarters q ON f.quarter_id = q.quarter_id
JOIN regions r ON f.region_id = r.region_id
JOIN departments d ON f.department_id = d.department_id
ORDER BY q.fiscal_year, q.quarter_num
"""


def fetch_dashboard_metrics() -> QueryExecutionResult:
    """Fixed, hand-written query (not LLM-generated) backing the dashboard
    charts -- it doesn't go through the SQL validator because it never
    carries untrusted input; it's one static SELECT returning the full
    (small) dataset for the frontend to filter/aggregate client-side."""
    return execute_safe_sql(DASHBOARD_METRICS_SQL)


def execute_safe_sql(safe_sql: str) -> QueryExecutionResult:
    start = time.monotonic()
    try:
        conn = _get_readonly_connection()
        try:
            cursor = conn.execute(safe_sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
        finally:
            conn.close()
        elapsed_ms = (time.monotonic() - start) * 1000
        return QueryExecutionResult(
            ok=True, columns=columns, rows=rows, elapsed_ms=elapsed_ms
        )
    except sqlite3.OperationalError as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        return QueryExecutionResult(
            ok=False, error=f"Query execution failed: {e}", elapsed_ms=elapsed_ms
        )
