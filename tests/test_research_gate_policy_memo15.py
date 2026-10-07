"""Publication policy integrity: no implicit downgrade to historical sanity."""
from copy import deepcopy

import pytest

from bellomberg.agents import action_validator as av
from bellomberg.agents import consigliere_multi as cm
from test_cablaggio_consigliere_multi import run_offline
from test_gate_research_memo15 import MEMO, CONTRACT, _bound_checks

INVALID = [None, False, True, "", "research-evidence-v2", [], {}]


@pytest.mark.parametrize("invalid", INVALID)
def test_invalid_policy_cannot_use_legacy_sanity_ok(invalid):
    contract = {**CONTRACT, "publication_gate_policy": invalid}
    before = deepcopy(contract)
    with pytest.raises(ValueError, match="publication gate policy") as error:
        av.assess_action_table(MEMO,
            sanity_exclusions=[{"ticker": "ZZTEST", "severity": "OK"}],
            publication_contract=contract, research_checks=None)
    assert "research-evidence-v2" not in str(error.value)
    assert contract == before


@pytest.mark.parametrize("invalid", INVALID)
@pytest.mark.parametrize("analysis_mode", [None, "fundamentals_research_v1"])
def test_present_invalid_policy_is_checked_before_mode(invalid, analysis_mode):
    with pytest.raises(ValueError, match="publication gate policy"):
        av.research_gate_enabled({"analysis_mode": analysis_mode, "publication_gate_policy": invalid})


def test_absence_and_none_contract_preserve_legacy_result():
    sanity = [{"ticker": "ZZTEST", "severity": "OK"}]
    baseline = av.assess_action_table(MEMO, sanity_exclusions=sanity)
    historic = av.assess_action_table(MEMO, sanity_exclusions=sanity,
        publication_contract={"analysis_mode": "fundamentals_research_v1"})
    assert baseline == historic and baseline[0]["status"] == "OPERATIVE"
    assert av.research_gate_enabled(None) is False
    assert av.research_gate_enabled({}) is False


def test_valid_version_still_requires_research_mode():
    assert av.research_gate_enabled({"publication_gate_policy": av.RESEARCH_GATE_POLICY}) is False
    assert av.research_gate_enabled(CONTRACT) is True


def test_valid_research_available_and_missing_evidence_unchanged():
    assert av.assess_action_table(MEMO, publication_contract=CONTRACT,
        research_checks=_bound_checks())[0]["status"] == "OPERATIVE"
    assert av.assess_action_table(MEMO, publication_contract=CONTRACT,
        research_checks=None)[0]["status"] == "CHECK_UNAVAILABLE"


@pytest.mark.parametrize("invalid", INVALID)
def test_resume_rejects_before_pipeline_or_paid_request(run_offline, monkeypatch, invalid):
    from bellomberg.agents.weekly_lifecycle import create_run
    monkeypatch.setattr(cm, "_weekly_contract", run_offline.native_weekly_contract)
    db = cm.MemoryDB()
    mandate = cm.mandato_o_esci()
    contract = cm._weekly_contract()
    contract["publication_gate_policy"] = invalid
    store = create_run(db, db.get_portfolio_summary(), mandate, contract, "it")
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("pipeline")
        raise AssertionError("Invalid saved policy must stop before pipeline")
    monkeypatch.setattr(cm, "_run_multi_agent", forbidden)
    with pytest.raises(ValueError, match="publication gate policy"):
        cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert calls == []
    assert run_offline.sondati == [] and run_offline.catturato == {} and run_offline.inviati == []
    assert store.status().get("request_journal_path") is None


def test_resume_absence_does_not_add_gate_policy():
    current = {"publication_gate_policy": av.RESEARCH_GATE_POLICY}
    assert "publication_gate_policy" not in cm._resume_publication_contract(current, {})
    assert cm._resume_publication_contract({}, CONTRACT)["publication_gate_policy"] == av.RESEARCH_GATE_POLICY
