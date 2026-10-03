-- Synthetic finance schema modeling an exec-dashboard style data warehouse.
-- All data is fabricated; nothing here is real company data.

CREATE TABLE regions (
    region_id   INTEGER PRIMARY KEY,
    region_name TEXT NOT NULL UNIQUE
);

CREATE TABLE departments (
    department_id   INTEGER PRIMARY KEY,
    department_name TEXT NOT NULL UNIQUE
);

CREATE TABLE quarters (
    quarter_id   INTEGER PRIMARY KEY,
    fiscal_year  INTEGER NOT NULL,
    quarter_num  INTEGER NOT NULL CHECK (quarter_num BETWEEN 1 AND 4),
    label        TEXT NOT NULL UNIQUE -- e.g. "2024-Q1"
);

-- One row per (quarter, region, department): the fact table holding
-- the ~20 financial drivers the resume bullet refers to.
CREATE TABLE financial_metrics (
    metric_id       INTEGER PRIMARY KEY,
    quarter_id      INTEGER NOT NULL REFERENCES quarters(quarter_id),
    region_id       INTEGER NOT NULL REFERENCES regions(region_id),
    department_id   INTEGER NOT NULL REFERENCES departments(department_id),
    revenue              REAL NOT NULL,
    cost_of_goods_sold   REAL NOT NULL,
    gross_profit         REAL NOT NULL,
    operating_expenses   REAL NOT NULL,
    operating_income     REAL NOT NULL,
    net_income           REAL NOT NULL,
    headcount            INTEGER NOT NULL,
    marketing_spend      REAL NOT NULL,
    rd_spend             REAL NOT NULL,
    capex                REAL NOT NULL,
    cash_balance          REAL NOT NULL,
    accounts_receivable   REAL NOT NULL,
    accounts_payable      REAL NOT NULL,
    inventory             REAL NOT NULL,
    customer_count         INTEGER NOT NULL,
    new_customers           INTEGER NOT NULL,
    churned_customers       INTEGER NOT NULL,
    avg_deal_size            REAL NOT NULL,
    gross_margin_pct          REAL NOT NULL,
    operating_margin_pct      REAL NOT NULL,
    UNIQUE (quarter_id, region_id, department_id)
);

CREATE INDEX idx_metrics_quarter ON financial_metrics(quarter_id);
CREATE INDEX idx_metrics_region ON financial_metrics(region_id);
CREATE INDEX idx_metrics_department ON financial_metrics(department_id);
