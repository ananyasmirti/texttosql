"""Builds finance.db: a small synthetic finance warehouse used to demo the
text-to-SQL copilot. All numbers are fabricated (seeded RNG) -- no real data.

Usage: python db/seed.py
"""
import random
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "finance.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

REGIONS = ["North America", "EMEA", "APAC", "LATAM"]
DEPARTMENTS = ["Sales", "Engineering", "Marketing", "Operations", "Support"]
YEARS = [2023, 2024, 2025]

random.seed(42)


def build():
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text())

    region_ids = {}
    for name in REGIONS:
        cur = conn.execute("INSERT INTO regions (region_name) VALUES (?)", (name,))
        region_ids[name] = cur.lastrowid

    dept_ids = {}
    for name in DEPARTMENTS:
        cur = conn.execute(
            "INSERT INTO departments (department_name) VALUES (?)", (name,)
        )
        dept_ids[name] = cur.lastrowid

    quarter_ids = {}
    for year in YEARS:
        for q in range(1, 5):
            if year == 2025 and q > 2:
                continue  # dataset ends at 2025-Q2 ("current")
            label = f"{year}-Q{q}"
            cur = conn.execute(
                "INSERT INTO quarters (fiscal_year, quarter_num, label) VALUES (?, ?, ?)",
                (year, q, label),
            )
            quarter_ids[label] = cur.lastrowid

    rows = []
    for label, quarter_id in quarter_ids.items():
        growth_factor = 1.0 + 0.03 * list(quarter_ids).index(label)
        for region in REGIONS:
            for dept in DEPARTMENTS:
                base = random.uniform(400_000, 1_600_000) * growth_factor
                revenue = round(base, 2)
                cogs = round(revenue * random.uniform(0.35, 0.55), 2)
                gross_profit = round(revenue - cogs, 2)
                opex = round(revenue * random.uniform(0.2, 0.4), 2)
                operating_income = round(gross_profit - opex, 2)
                net_income = round(operating_income * random.uniform(0.7, 0.95), 2)
                headcount = random.randint(8, 120)
                marketing_spend = round(revenue * random.uniform(0.02, 0.1), 2)
                rd_spend = round(revenue * random.uniform(0.03, 0.15), 2)
                capex = round(revenue * random.uniform(0.01, 0.05), 2)
                cash_balance = round(random.uniform(100_000, 5_000_000), 2)
                ar = round(revenue * random.uniform(0.1, 0.25), 2)
                ap = round(revenue * random.uniform(0.08, 0.2), 2)
                inventory = round(revenue * random.uniform(0.0, 0.15), 2)
                customer_count = random.randint(20, 2000)
                new_customers = random.randint(1, 150)
                churned_customers = random.randint(0, 60)
                avg_deal_size = round(revenue / max(customer_count, 1), 2)
                gross_margin_pct = round(gross_profit / revenue * 100, 2)
                operating_margin_pct = round(operating_income / revenue * 100, 2)

                rows.append(
                    (
                        quarter_id,
                        region_ids[region],
                        dept_ids[dept],
                        revenue,
                        cogs,
                        gross_profit,
                        opex,
                        operating_income,
                        net_income,
                        headcount,
                        marketing_spend,
                        rd_spend,
                        capex,
                        cash_balance,
                        ar,
                        ap,
                        inventory,
                        customer_count,
                        new_customers,
                        churned_customers,
                        avg_deal_size,
                        gross_margin_pct,
                        operating_margin_pct,
                    )
                )

    conn.executemany(
        """
        INSERT INTO financial_metrics (
            quarter_id, region_id, department_id,
            revenue, cost_of_goods_sold, gross_profit, operating_expenses,
            operating_income, net_income, headcount, marketing_spend, rd_spend,
            capex, cash_balance, accounts_receivable, accounts_payable, inventory,
            customer_count, new_customers, churned_customers, avg_deal_size,
            gross_margin_pct, operating_margin_pct
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM financial_metrics").fetchone()[0]
    print(f"Seeded {n} financial_metrics rows into {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    build()
