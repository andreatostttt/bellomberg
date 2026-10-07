"""New runs preserve total NAV and serialized usage without guessing missing totals."""
import json

import pytest

from bellomberg.core.llm_client import _usage_da_json
from bellomberg.agents.weekly_lifecycle import create_run
from bellomberg.storage import memory_db
from test_cablaggio_consigliere_multi import run_offline


@pytest.mark.parametrize("details,expected_uncached", [
    ({"cached_tokens": 600, "cache_write_tokens": 100}, 300), ({}, None),
])
@pytest.mark.parametrize("reasoning", [None, 0, 15])
def test_usage_serialization_preserves_totals_and_cache_semantics(details, expected_uncached, reasoning):
    usage = _usage_da_json({"prompt_tokens": 1000, "completion_tokens": 40,
        "prompt_tokens_details": details, "completion_tokens_details": {"reasoning_tokens": reasoning},
        "cost": 0.012})
    usage.request_id = "synthetic-request"
    saved = json.loads(json.dumps(usage.to_dict()))
    assert saved["prompt_tokens"] == 1000 and saved["completion_tokens"] == 40
    assert saved["input_tokens"] == expected_uncached
    assert saved["output_tokens"] == 40 and saved["reasoning_tokens"] == reasoning
    assert saved["cost_usd"] == 0.012 and saved["request_ids"] == ["synthetic-request"]


@pytest.mark.parametrize("payload,prompt,completion", [({}, None, None),
    ({"prompt_tokens": 0, "completion_tokens": 0}, 0, 0)])
def test_usage_missing_totals_do_not_become_zero(payload, prompt, completion):
    saved = _usage_da_json(payload).to_dict()
    assert saved["prompt_tokens"] is prompt and saved["completion_tokens"] is completion
    assert saved["input_tokens"] is None


@pytest.mark.parametrize("nav,expected", [(1000, 1000), (0, 0), (None, None)])
def test_new_weekly_persists_total_nav_without_adding_cash_or_substituting_invested(run_offline, nav, expected):
    db = memory_db.MemoryDB()
    portfolio = {"positions": [], "n_positions": 0, "totale_valore_mercato_eur": 800,
                 "cash_eur": 200}
    if nav is not None:
        portfolio["nav_total_eur"] = nav
    store = create_run(db, portfolio, {}, {"analysis_mode": "fundamentals_research_v1"}, "it")
    with db._conn() as conn:
        row = conn.execute("SELECT portfolio_nav_eur FROM memos WHERE id=?", (store.memo_id,)).fetchone()
    assert row[0] == expected
    assert store.context["portfolio"] == portfolio
