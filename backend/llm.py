"""Wraps the two Groq API calls in the pipeline:

  generate_sql()      NL question (+ history) + schema -> candidate SQL
  summarize_result()   NL question + query result rows -> NL answer

Two separate calls instead of one "do everything" call is a deliberate
trade-off: it costs an extra round-trip of latency/tokens, but it means the
model never sees (and can't leak) the fact that SQL exists at all in the
final answer, and a failure in summarization can't corrupt SQL generation
or vice versa -- each call has one job and one failure mode to reason about.

Provider note: started on Gemini's free tier, but its top Flash model caps
out at 20 requests/day on the free tier -- not enough for iterative
development plus an eval run plus manual testing. Switched to Groq, whose
free tier is generous enough (thousands of requests/day) to actually build
and test against. Both are OpenAI-style chat APIs under the hood, so the
swap only touched this file.
"""
import os
import time

import groq
from dotenv import load_dotenv

from .schema_context import SCHEMA_DESCRIPTION

load_dotenv()

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0

_client = None


def _get_client() -> groq.Groq:
    global _client
    if _client is None:
        _client = groq.Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


class LLMError(Exception):
    """Raised when the Groq API call fails after all retries."""


def _call_with_retry(*, system_prompt: str, user_messages: list[dict], max_tokens: int) -> str:
    client = _get_client()
    messages = [{"role": "system", "content": system_prompt}] + user_messages

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            content = response.choices[0].message.content
            if not content:
                raise LLMError(f"Empty response from Groq (finish_reason={response.choices[0].finish_reason})")
            return content.strip()
        except (groq.RateLimitError, groq.APIConnectionError) as e:
            last_exc = e
            time.sleep(BASE_BACKOFF_SECONDS * (2**attempt))
        except groq.APIStatusError as e:
            if e.status_code >= 500:
                last_exc = e
                time.sleep(BASE_BACKOFF_SECONDS * (2**attempt))
            else:
                raise LLMError(f"Groq API rejected the request: {e}") from e
    raise LLMError(f"Groq API call failed after {MAX_RETRIES} retries: {last_exc}")


SQL_SYSTEM_PROMPT = f"""You translate natural-language questions about company
financials into a single SQLite SELECT query.

{SCHEMA_DESCRIPTION}

Rules:
- Output ONLY the SQL query, no explanation, no markdown code fences.
- Only ever write a single SELECT statement. Never write INSERT, UPDATE,
  DELETE, DROP, ALTER, or more than one statement.
- Only use the tables and columns listed above. Never invent column names.
- The most recent quarter in the data is 2025-Q2 -- treat this as "now".
  "last quarter", "the most recent quarter", and "this quarter" ALL refer
  to 2025-Q2 itself (the latest completed quarter), NOT the quarter before
  it. Only go back an additional quarter if the user explicitly says
  "the quarter before last" or names a specific earlier quarter/year.
- If the question cannot be answered with the available schema, output
  exactly: NO_QUERY: <one sentence reason>
"""


def generate_sql(question: str, history: list[dict]) -> str:
    """history is a list of {"role": "user"|"assistant", "content": str}
    prior turns, used so follow-ups like "now break that down by region"
    resolve against the previous question.
    """
    user_messages = history + [{"role": "user", "content": question}]
    raw = _call_with_retry(
        system_prompt=SQL_SYSTEM_PROMPT, user_messages=user_messages, max_tokens=512
    )
    # Models sometimes wrap output in ```sql fences despite instructions; strip defensively.
    return raw.strip().removeprefix("```sql").removeprefix("```").removesuffix("```").strip()


SUMMARY_SYSTEM_PROMPT = """You summarize SQL query results for a business
executive. Be concise (2-4 sentences), lead with the headline number, use
plain language, and format currency/percentages readably. Do not mention
SQL, tables, or columns -- the reader only cares about the business answer.
"""


def summarize_result(question: str, columns: list[str], rows: list[tuple]) -> str:
    preview_rows = rows[:50]  # cap tokens sent for summarization
    table_text = ", ".join(columns) + "\n"
    table_text += "\n".join(str(r) for r in preview_rows)
    truncated_note = (
        f"\n(... {len(rows) - 50} more rows not shown)" if len(rows) > 50 else ""
    )

    user_messages = [
        {
            "role": "user",
            "content": f"Question: {question}\n\nResult data:\n{table_text}{truncated_note}",
        }
    ]
    return _call_with_retry(
        system_prompt=SUMMARY_SYSTEM_PROMPT, user_messages=user_messages, max_tokens=300
    )
