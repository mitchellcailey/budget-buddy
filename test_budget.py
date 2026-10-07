import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("budget_buddy", Path(__file__).parent / "budget-buddy.py")
bb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bb)


@pytest.fixture
def df(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(
        "Date,Description,Amount\n"
        "2025-01-02,Whole Foods,80\n"
        "2025-01-09,Whole Foods,20\n"
        "2025-02-01,Netflix,15\n"
        "2025-02-02,Paycheck,-2000\n"
        "bad-date,Oops,5\n"
    )
    return bb.load_transactions(str(p))


def test_load_normalizes_and_drops_bad_rows(df):
    assert len(df) == 4
    assert {"date", "description", "amount"} <= set(df.columns)


def test_load_rejects_missing_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="missing required"):
        bb.load_transactions(str(p))


def test_tools_ignore_income(df):
    top = bb.tool_top_merchants(df, 5)
    assert top[0] == {"description": "Whole Foods", "total": 100.0, "count": 2}
    assert all(m["description"] != "Paycheck" for m in top)
    assert bb.tool_monthly_totals(df) == [{"month": "2025-01", "total": 100.0}, {"month": "2025-02", "total": 15.0}]
    assert len(bb.tool_search_transactions(df, "netflix")) == 1


def test_build_report_runs_tool_loop_then_parses(df):
    report_json = bb.BudgetReport(
        period="2025-01-02 to 2025-02-02", total_spent=115.0,
        categories=[bb.CategoryBudget(category="Groceries", spent=100, suggested_budget=90, transaction_count=2)],
        saving_tips=["Buy store brand."], summary="ok",
    ).model_dump_json()
    tool_turn = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="t1", name="monthly_totals", input={})],
    )
    final_turn = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=report_json)])
    calls = []

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                calls.append(kw)
                return [tool_turn, final_turn][len(calls) - 1]

    report = bb.build_report(df, client=FakeClient())
    assert report.categories[0].category == "Groceries"
    assert len(calls) == 2
    assert calls[0]["thinking"] == {"type": "adaptive"}
    assert calls[1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"


def test_format_report_lists_tips():
    r = bb.BudgetReport(period="p", total_spent=1, categories=[], saving_tips=["a"], summary="s")
    assert "1. a" in bb.format_report(r)
