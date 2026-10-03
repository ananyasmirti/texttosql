"""Executive finance dashboard with the text-to-SQL copilot embedded as a
sidebar assistant. Mirrors the actual production pattern this project is
modeled on: analysts watch trend/breakdown charts for the metrics they
already expect, and reach for the chat panel only for an ad-hoc question
the fixed charts don't answer.

Both the charts (GET /metrics, a fixed query) and the chat (POST /query,
LLM-generated SQL) are served by the same FastAPI backend -- this page is
a pure presentation layer with no direct DB access.
"""
import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

# Fixed categorical color order (validated palette, slots 1-5) -- color
# follows the entity, not its position, so filtering never repaints survivors.
REGION_COLORS = {
    "North America": "#2a78d6",  # slot 1 blue
    "EMEA": "#eb6834",           # slot 2 orange
    "APAC": "#1baf7a",           # slot 3 aqua
    "LATAM": "#eda100",          # slot 4 yellow
}
DEPARTMENT_COLORS = {
    "Sales": "#2a78d6",
    "Engineering": "#eb6834",
    "Marketing": "#1baf7a",
    "Operations": "#eda100",
    "Support": "#e87ba4",        # slot 5 magenta
}
MUTED_INK = "#898781"

st.set_page_config(page_title="Finance Dashboard", page_icon="📊", layout="wide")

# Targeted, minimal CSS: tighten Streamlit's default top whitespace and give
# the KPI tiles a deliberate card treatment (thin border, one consistent
# corner-radius -- no shadow, no gradient) since they're the one place on
# this page where elevation communicates real hierarchy (headline numbers).
st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    div[data-testid="stMetric"] {
        background: #f2f1ed;
        border: 1px solid rgba(11,11,11,0.08);
        border-radius: 10px;
        padding: 1rem 1.1rem;
    }
    div[data-testid="stMetricLabel"] { color: #52514e; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60)
def load_metrics() -> pd.DataFrame:
    resp = requests.get(f"{BACKEND_URL}/metrics", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data["ok"]:
        raise RuntimeError(data["error"])
    return pd.DataFrame(data["rows"], columns=data["columns"])


def format_currency(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.1f}K"
    return f"${value:,.0f}"


def apply_chart_theme(fig):
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=MUTED_INK,
        legend_title_text="",
        margin=dict(l=10, r=10, t=30, b=10),
    )
    fig.update_xaxes(gridcolor="rgba(137,135,129,0.15)", title=None)
    fig.update_yaxes(gridcolor="rgba(137,135,129,0.15)", title=None)
    return fig


try:
    df = load_metrics()
except (requests.RequestException, RuntimeError) as e:
    st.error(f"Could not load dashboard data from the backend: {e}")
    st.stop()

quarter_order = sorted(df["quarter_label"].unique())  # "YYYY-QN" sorts chronologically

st.title("📊 Finance Overview")

filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 3])
with filter_col1:
    selected_regions = st.multiselect(
        "Region", options=list(REGION_COLORS), default=list(REGION_COLORS)
    )
with filter_col2:
    selected_departments = st.multiselect(
        "Department", options=list(DEPARTMENT_COLORS), default=list(DEPARTMENT_COLORS)
    )
with filter_col3:
    quarter_range = st.select_slider(
        "Quarter range",
        options=quarter_order,
        value=(quarter_order[0], quarter_order[-1]),
    )

mask = (
    df["region_name"].isin(selected_regions)
    & df["department_name"].isin(selected_departments)
    & (df["quarter_label"] >= quarter_range[0])
    & (df["quarter_label"] <= quarter_range[1])
)
filtered = df[mask]

if filtered.empty:
    st.warning("No data for the current filter selection.")
    st.stop()

latest_quarter = filtered["quarter_label"].max()

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("Total Revenue", format_currency(filtered["revenue"].sum()))
kpi2.metric("Net Income", format_currency(filtered["net_income"].sum()))
kpi3.metric(
    f"Headcount ({latest_quarter})",
    f"{filtered.loc[filtered['quarter_label'] == latest_quarter, 'headcount'].sum():,}",
)
kpi4.metric("Avg Operating Margin", f"{filtered['operating_margin_pct'].mean():.1f}%")

st.divider()

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("Revenue by quarter")
    trend = (
        filtered.groupby(["quarter_label", "region_name"], as_index=False)["revenue"]
        .sum()
    )
    fig_trend = px.line(
        trend,
        x="quarter_label",
        y="revenue",
        color="region_name",
        category_orders={"quarter_label": quarter_order, "region_name": list(REGION_COLORS)},
        color_discrete_map=REGION_COLORS,
        markers=True,
    )
    fig_trend.update_traces(line_width=2, marker_size=8)
    st.plotly_chart(apply_chart_theme(fig_trend), use_container_width=True)

with chart_col2:
    st.subheader("Avg operating margin by department")
    margin = (
        filtered.groupby("department_name", as_index=False)["operating_margin_pct"]
        .mean()
        .sort_values("operating_margin_pct", ascending=True)
    )
    fig_margin = px.bar(
        margin,
        x="operating_margin_pct",
        y="department_name",
        orientation="h",
        color="department_name",
        category_orders={"department_name": list(DEPARTMENT_COLORS)},
        color_discrete_map=DEPARTMENT_COLORS,
    )
    fig_margin.update_traces(showlegend=False)
    st.plotly_chart(apply_chart_theme(fig_margin), use_container_width=True)

with st.expander("View filtered data as a table"):
    st.dataframe(filtered, use_container_width=True)


# --- Sidebar: text-to-SQL copilot, for ad-hoc questions the fixed charts
# above don't answer -----------------------------------------------------
with st.sidebar:
    st.header("💬 Ask the copilot")
    st.caption("Ask a specific financial question in plain English.")

    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = None
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sql"):
                with st.expander("SQL"):
                    st.code(msg["sql"], language="sql")

    question = st.chat_input("e.g. Which department had the lowest margin?")

    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    resp = requests.post(
                        f"{BACKEND_URL}/query",
                        json={
                            "question": question,
                            "conversation_id": st.session_state.conversation_id,
                        },
                        timeout=30,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except requests.RequestException as e:
                    st.error(f"Could not reach the backend: {e}")
                    st.stop()

            st.session_state.conversation_id = data["conversation_id"]

            if data["status"] == "ok":
                answer = data.get("answer") or "Here are the raw results:"
                st.markdown(answer)
                if data.get("columns") and data.get("rows"):
                    st.dataframe(
                        pd.DataFrame(data["rows"], columns=data["columns"]),
                        use_container_width=True,
                    )
                with st.expander("SQL"):
                    st.code(data["sql"], language="sql")
                st.session_state.messages.append(
                    {"role": "assistant", "content": answer, "sql": data.get("sql")}
                )
            elif data["status"] == "clarification_needed":
                st.info(data["answer"])
                st.session_state.messages.append(
                    {"role": "assistant", "content": data["answer"]}
                )
            else:
                st.error(data.get("error", "Something went wrong."))
                st.session_state.messages.append(
                    {"role": "assistant", "content": f"Error: {data.get('error')}"}
                )

    if st.button("Reset conversation"):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.rerun()
