"""Serializes the DB schema + column descriptions into a compact text block
the LLM is given as context. Kept hand-written (not introspected from
sqlite_master) so we control exactly what the model sees and can attach
business-meaning descriptions, not just column names.
"""

SCHEMA_DESCRIPTION = """
Tables (SQLite). All monetary values are in USD. This is a star-schema style
finance warehouse: financial_metrics is the fact table, the others are
dimension lookup tables.

regions(region_id, region_name)
    -- region_name is one of: North America, EMEA, APAC, LATAM

departments(department_id, department_name)
    -- department_name is one of: Sales, Engineering, Marketing, Operations, Support

quarters(quarter_id, fiscal_year, quarter_num, label)
    -- label is formatted "YYYY-QN", e.g. "2024-Q1". Data spans 2023-Q1 through 2025-Q2.
    -- quarter_num is 1-4.

financial_metrics(
    metric_id, quarter_id, region_id, department_id,
    revenue, cost_of_goods_sold, gross_profit, operating_expenses,
    operating_income, net_income, headcount, marketing_spend, rd_spend,
    capex, cash_balance, accounts_receivable, accounts_payable, inventory,
    customer_count, new_customers, churned_customers, avg_deal_size,
    gross_margin_pct, operating_margin_pct
)
    -- One row per (quarter, region, department) combination.
    -- gross_margin_pct and operating_margin_pct are already expressed as
    --   percentages (e.g. 42.5 means 42.5%), not fractions.
    -- avg_deal_size is already an average, but only WITHIN a single
    --   (quarter, region, department) row. It is NOT pre-aggregated across
    --   regions/departments -- if a question asks for "average deal size"
    --   for a department across multiple regions (or without specifying a
    --   region), you must still wrap it in AVG(avg_deal_size) to combine
    --   the per-region rows. Do not assume the column name means no further
    --   aggregation is needed.
    -- Join to regions on region_id, departments on department_id,
    --   quarters on quarter_id.
"""

ALLOWED_TABLES = {
    "regions",
    "departments",
    "quarters",
    "financial_metrics",
}

ALLOWED_COLUMNS = {
    "regions": {"region_id", "region_name"},
    "departments": {"department_id", "department_name"},
    "quarters": {"quarter_id", "fiscal_year", "quarter_num", "label"},
    "financial_metrics": {
        "metric_id",
        "quarter_id",
        "region_id",
        "department_id",
        "revenue",
        "cost_of_goods_sold",
        "gross_profit",
        "operating_expenses",
        "operating_income",
        "net_income",
        "headcount",
        "marketing_spend",
        "rd_spend",
        "capex",
        "cash_balance",
        "accounts_receivable",
        "accounts_payable",
        "inventory",
        "customer_count",
        "new_customers",
        "churned_customers",
        "avg_deal_size",
        "gross_margin_pct",
        "operating_margin_pct",
    },
}
