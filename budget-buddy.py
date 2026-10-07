#!/usr/bin/env python3
"""Budget Buddy: categorize spending from transactions.csv and get a budget with saving tips.

Pandas does the number crunching. Claude (Sonnet, adaptive thinking) runs a tool-use
loop over the data and returns a schema-validated budget as structured output.
"""
import argparse
import json
import os
import sys

import anthropic
import pandas as pd
from anthropic import transform_schema
from pydantic import BaseModel, Field

MODEL = os.environ.get("BUDGET_BUDDY_MODEL", "claude-sonnet-5-5")
MAX_TURNS = 10
REQUIRED_COLUMNS = {"date", "description", "amount"}


class CategoryBudget(BaseModel):
    category: str
    spent: float = Field(description="Total actually spent in this category")
    suggested_budget: float = Field(description="Recommended spend for the same period")
    transaction_count: int


class BudgetReport(BaseModel):
    period: str = Field(description="Date range covered, e.g. 2025-01-01 to 2025-03-31")
    total_spent: float
    categories: list[CategoryBudget]
    saving_tips: list[str] = Field(description="Specific, actionable tips grounded in the data")
    summary: str


# --------------------------------------------------------------------------- data


def load_transactions(path: str) -> pd.DataFrame:
    """Load a CSV with date, description, amount (spending positive) columns."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing required column(s): {', '.join(sorted(missing))}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["date", "amount"]).reset_index(drop=True)
    df["description"] = df["description"].astype(str).str.strip()
    return df


def spending_overview(df: pd.DataFrame) -> dict:
    """Cheap facts used to open the conversation."""
    return {
        "rows": len(df),
        "start": df["date"].min().date().isoformat() if len(df) else None,
        "end": df["date"].max().date().isoformat() if len(df) else None,
        "total_spent": round(float(df.loc[df["amount"] > 0, "amount"].sum()), 2),
        "columns": list(df.columns),
    }


# ------------------------------------------------------------------------- tools


def tool_top_merchants(df: pd.DataFrame, limit: int = 15) -> list[dict]:
    spend = df[df["amount"] > 0]
    grouped = spend.groupby("description")["amount"].agg(["sum", "count"])
    top = grouped.sort_values("sum", ascending=False).head(limit)
    return [
        {"description": name, "total": round(float(r["sum"]), 2), "count": int(r["count"])}
        for name, r in top.iterrows()
    ]


def tool_monthly_totals(df: pd.DataFrame) -> list[dict]:
    spend = df[df["amount"] > 0]
    monthly = spend.groupby(spend["date"].dt.to_period("M"))["amount"].sum()
    return [{"month": str(m), "total": round(float(t), 2)} for m, t in monthly.items()]


def tool_search_transactions(df: pd.DataFrame, query: str, limit: int = 25) -> list[dict]:
    hits = df[df["description"].str.contains(query, case=False, regex=False)].head(limit)
    return [
        {"date": r.date.date().isoformat(), "description": r.description, "amount": float(r.amount)}
        for r in hits.itertuples()
    ]


TOOL_DEFS = [
    {
        "name": "top_merchants",
        "description": "Merchants/descriptions ranked by total spend, with transaction counts.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "How many to return"}},
            "required": ["limit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "monthly_totals",
        "description": "Total spending per calendar month.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_transactions",
        "description": "Case-insensitive substring search over transaction descriptions.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


def run_tool(df: pd.DataFrame, name: str, args: dict):
    if name == "top_merchants":
        return tool_top_merchants(df, int(args.get("limit", 15)))
    if name == "monthly_totals":
        return tool_monthly_totals(df)
    if name == "search_transactions":
        return tool_search_transactions(df, str(args["query"]))
    raise ValueError(f"unknown tool: {name}")


# ------------------------------------------------------------------------- agent

SYSTEM = (
    "You are Budget Buddy, a practical personal-finance analyst. Positive amounts are "
    "spending; negative amounts are income or refunds. Use the tools to inspect the "
    "transactions, assign every spending line to a sensible category (Groceries, Dining, "
    "Transport, Housing, Utilities, Subscriptions, Entertainment, Health, Shopping, Other), "
    "then produce a realistic budget with one entry per category and saving tips that cite "
    "specific merchants or amounts from the data. Do not invent transactions."
)


def parse_report(response) -> BudgetReport:
    text = next(b.text for b in response.content if b.type == "text")
    return BudgetReport.model_validate_json(text)


def build_report(df: pd.DataFrame, client: anthropic.Anthropic | None = None, model: str = MODEL) -> BudgetReport:
    """Tool-use loop: Claude queries the data, then returns a BudgetReport as structured output."""
    client = client or anthropic.Anthropic()
    messages = [{
        "role": "user",
        "content": f"Overview of my transactions: {json.dumps(spending_overview(df))}\n"
                   "Categorize my spending and build me a budget with saving tips.",
    }]
    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            tools=TOOL_DEFS,
            output_config={"format": {"type": "json_schema", "schema": transform_schema(BudgetReport)}},
            messages=messages,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Response was cut off (max_tokens); try a smaller CSV.")
        if response.stop_reason != "tool_use":
            return parse_report(response)

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                out = {"type": "tool_result", "tool_use_id": block.id,
                       "content": json.dumps(run_tool(df, block.name, block.input))}
            except Exception as exc:  # report tool failures back to the model
                out = {"type": "tool_result", "tool_use_id": block.id,
                       "content": f"error: {exc}", "is_error": True}
            results.append(out)
        messages.append({"role": "user", "content": results})
    raise RuntimeError(f"No final answer after {MAX_TURNS} turns.")


def format_report(report: BudgetReport) -> str:
    lines = [f"Budget for {report.period}", f"Total spent: ${report.total_spent:,.2f}", "",
             f"{'Category':<18}{'Spent':>12}{'Budget':>12}{'Txns':>6}"]
    for c in sorted(report.categories, key=lambda c: -c.spent):
        lines.append(f"{c.category:<18}{c.spent:>12,.2f}{c.suggested_budget:>12,.2f}{c.transaction_count:>6}")
    lines += ["", report.summary, "", "Saving tips:"]
    lines += [f"  {i}. {tip}" for i, tip in enumerate(report.saving_tips, 1)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Categorize spending and get a budget with saving tips.")
    parser.add_argument("csv", nargs="?", default="transactions.csv", help="CSV with date,description,amount")
    parser.add_argument("--json", action="store_true", help="print the raw JSON report")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)

    try:
        df = load_transactions(args.csv)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if df.empty:
        print("error: no valid transactions found", file=sys.stderr)
        return 1

    try:
        report = build_report(df, model=args.model)
    except anthropic.AuthenticationError:
        print("error: invalid or missing ANTHROPIC_API_KEY (see .env.example)", file=sys.stderr)
        return 1
    except (anthropic.APIError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(report.model_dump_json(indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
