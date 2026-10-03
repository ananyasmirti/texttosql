# Finance Text-to-SQL Copilot

A conversational copilot that answers natural-language questions about
company financials by generating SQL, validating it for safety, executing
it against a read-only database, and summarizing the result in plain
language. Built as a from-scratch rebuild of a similar system, to have a
concrete, speakable project for technical interviews.

```
Streamlit dashboard (charts + filters)  --> GET /metrics  --> fixed SQL, read-only SQLite
          |
          +-- sidebar copilot (chat) --> POST /query --> FastAPI --> Groq LLM (generates SQL)
                                                                           |
                                                                           v
                                                           SQL validation layer (sqlglot allowlist)
                                                                           |
                                                                           v
                                                           SQLite (read-only connection, row/time limits)
                                                                           |
                                                                           v
                                                           Result -> optional NL summary (2nd LLM call)
```

The dashboard (`frontend/app.py`) is a pure presentation layer with no
direct DB access -- both the charts and the chat panel go through the
FastAPI backend, just via two different endpoints: `GET /metrics` serves a
fixed, hand-written query for the standard charts (no LLM involved, so no
validator needed -- it never carries untrusted input), while `POST /query`
is the LLM-generated-SQL path behind the sidebar copilot, protected by the
full validation/safety layer below.

## Data

`db/schema.sql` + `db/seed.py` build a small synthetic finance warehouse
(star schema: `financial_metrics` fact table + `regions`/`departments`/
`quarters` dimensions), 200 rows across 4 regions x 5 departments x 10
quarters (2023-Q1 through 2025-Q2), ~20 financial metrics per row
(revenue, margins, headcount, cash, churn, etc). All values are fabricated
with a seeded RNG -- nothing here is real data.

## Running it

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python db/seed.py                 # builds db/finance.db

cp .env.example .env                        # fill in GROQ_API_KEY

.venv/bin/uvicorn backend.main:app --reload &
.venv/bin/streamlit run frontend/app.py
```

Then open the Streamlit URL it prints. The main panel is a filterable
dashboard (region/department/quarter filters, KPI tiles, revenue trend and
margin-by-department charts); the sidebar is the copilot for anything the
fixed charts don't answer -- try:
- "What was total revenue by region in Q1 2024?"
- "Now just show EMEA" (follow-up, uses conversation memory)
- "Which department had the lowest operating margin in 2024?"

## Testing

Two complementary test layers, deliberately split by what they verify:

- **`tests/test_fault_injection.py`** (`pytest tests/`) -- mocks the LLM
  boundary to deterministically test how *our* code reacts to failure:
  LLM errors, hallucinated columns, DROP/DELETE attempts, statement-
  stacking injection, execution errors, graceful degradation when
  summarization fails. Zero API calls, zero flakiness.
- **`backend/eval/`** (`python -m backend.eval.run_eval`) -- runs 22
  labeled NL questions through the real pipeline (real LLM call included)
  and checks execution accuracy against reference SQL. Currently
  **22/22 (100%)**, after two real fixes surfaced during development (see
  below). This is the "does it actually work well enough to ship" signal.

## Safety layer (`backend/validator.py`, `backend/db.py`)

The LLM is treated as an untrusted generator. Defense in depth:
1. Parse with `sqlglot`; reject anything that doesn't parse.
2. Reject anything that isn't exactly one statement (blocks statement-
   stacking injection like `SELECT ...; DROP TABLE ...`).
3. Reject anything that isn't a `SELECT` (blocks DDL/DML entirely).
4. Reject any table/column not in an explicit allowlist (blocks
   hallucinated schema references).
5. Force a `LIMIT` if missing, cap it if excessive (bounds result size).
6. Execute only over a `mode=ro` SQLite connection -- even if a bug let
   something past the validator, the OS-level file handle still refuses
   writes (confirmed in `test_destructive_sql_is_blocked_before_execution`
   and manually: a raw `DELETE` against the connection fails with
   `attempt to write a readonly database`).

## Two real issues the eval harness surfaced (and the fixes)

1. **"Last quarter" ambiguity.** The model interpreted "last quarter"
   as *the quarter before* the most recent one in the data, when the
   intent was *the most recent completed quarter itself*. Fixed by
   making the system prompt explicitly state that "last quarter" /
   "most recent quarter" / "this quarter" all resolve to the latest
   quarter in the data, not one further back.
2. **Column-naming trap.** The `avg_deal_size` column is only a
   per-(quarter, region, department) average -- not pre-aggregated
   across regions. The model saw a column already named "avg" and
   skipped wrapping it in `AVG()` when a question asked for a
   department's average deal size across all regions, undercounting by
   returning unaggregated per-region rows. Fixed by adding an explicit
   note in the schema description warning the model not to assume this
   column needs no further aggregation.

## Deployment

Backend (Render) + frontend (Streamlit Community Cloud) as two separate
services, connected by one env var:

1. **Backend on Render**: New + -> Blueprint, point at this repo (uses
   `render.yaml`). Set the `GROQ_API_KEY` secret in the Render dashboard
   when prompted. `render.yaml`'s build step runs `db/seed.py` so the
   SQLite file is regenerated fresh on every deploy -- it's not committed
   (deterministic synthetic data, no reason to version the binary). Copy
   the resulting service URL (`https://<name>.onrender.com`).
2. **Frontend on Streamlit Community Cloud**: New app -> this repo,
   main file path `frontend/app.py`. In App settings -> Secrets, add:
   ```toml
   BACKEND_URL = "https://<your-render-service>.onrender.com"
   ```
3. Free tier on both sleeps after inactivity -- hit the Render URL a
   minute or two before a live demo to wake it, since the first request
   after sleep has a cold-start delay.

## Trade-offs

- **SQLite over Postgres**: zero infra, fast to stand up for a demo.
  Loses real connection pooling and role-based grants -- the read-only
  guarantee here is enforced by a `mode=ro` file handle instead of a DB
  role.
- **Two LLM calls (generate SQL, then summarize) instead of one**: costs
  extra latency/tokens, but keeps failure domains separate (a
  summarization failure degrades to raw rows instead of corrupting SQL
  generation) and means the model never has to be told not to mention
  "SQL" to the end user -- it just never sees the SQL concept in that call.
- **Groq instead of a paid LLM API**: started on Gemini's free tier, but
  its top Flash model caps free usage at 20 requests/day -- not enough for
  iterative development. Groq's free tier is generous enough to actually
  build and test against; the provider-specific code is isolated to
  `backend/llm.py` so the swap took minutes.
