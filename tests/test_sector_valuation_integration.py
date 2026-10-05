"""Same acquired case at public tool and cache boundaries, with synthetic data only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

from bellomberg.valuation.method_registry import get_method_requirements, select_valuation_method
from bellomberg.valuation.sector_analysis import prepare_sector_analysis, revise_sector_analysis
from bellomberg.valuation.valuation_profile import resolve_valuation_profile


DAY = "2026-09-10"
SYMBOL = "SYNTH_OUTSIDE_PORTFOLIO"


def synthetic_bundle(*, model="software", day=DAY, complete=True, context=None):
    """These records exercise acquisition contracts, not economic-source verification."""
    evidence = [{"field": key, "value": value, "source_id": "synthetic-issuer", "as_of": day}
                for key, value in (("instrument", "equity"), ("business_model", model))]
    decision = select_valuation_method(resolve_valuation_profile(
        SYMBOL, evidence=evidence, vehicle_registry=None, as_of=day))
    records = [{"field": r["field"], "entity": SYMBOL, "period": "FY2025", "unit": "synthetic-unit",
                "accounting_basis": "synthetic-basis", "source_id": "synthetic-issuer", "as_of": day,
                "value": {"synthetic_record": True}}
               for r in get_method_requirements(decision["method_id"])["fields"]]
    providers = {"profile": lambda *args, **kwargs: {
        "status": "ok", "source_id": "synthetic-profile", "as_of": day,
        "data": {"info": {"currency": "USD"}, "evidence": evidence, "vehicle_registry": None}}}
    if complete:
        providers["method_inputs"] = lambda *args, **kwargs: {
            "status": "ok", "source_id": "synthetic-method-inputs", "as_of": day,
            "data": {"synthetic": True}, "records": records}
    return prepare_sector_analysis(SYMBOL, as_of=day, providers=providers,
                                   user_context={"analysis_context": context or {}})


def documented_payload(bundle, path, *, generation="synthetic-generation"):
    """An explicitly synthetic future adapter result; real S2 legacy remains draft."""
    method = bundle["decision"]["method_id"]
    return {"ok": True, "ticker": SYMBOL, "engine": bundle["case"]["route"], "path": str(path),
            "price": 100.0, "fair_value_weighted": 120.0, "upside_pct": 20.0,
            "valuation_decision": deepcopy(bundle["decision"]), "snapshot_id": bundle["snapshot_id"],
            "generation_id": generation, "acquisition_snapshot": deepcopy(bundle),
            "workbook_sha256": sha256(path.read_bytes()).hexdigest(),
            "analytical_quality": {"status": "DOCUMENTATA", "method_id": method, "issues": [],
                                   "snapshot": {"as_of": bundle["case"]["as_of"]}},
            "sanity": {"severity": "OK", "status": "ok", "method_id": method},
            "input_consumption": {"status": "complete", "unconsumed_fields": [],
                                  "consumed_fields": [r["field"] for r in bundle["case"]["records"]],
                                  "consumed_records": [{"record_index":i, **{key:r.get(key) for key in
                                      ("field","scenario","driver","entity","period","source_id")}}
                                      for i,r in enumerate(bundle["case"]["records"])]}}


def documented_operating_bundle():
    """Frozen economic records for positive tests of the real compiler contract."""
    from test_sector_operating_drivers import bundle_for
    return bundle_for(symbol=SYMBOL)


@pytest.fixture
def isolated_tools(monkeypatch, tmp_path):
    from bellomberg.agents import agent_tools, chat_tools
    from bellomberg.valuation import dcf_engine
    import yfinance

    def forbidden(*args, **kwargs):
        pytest.fail("Sector integration attempted a real provider/network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(yfinance, "Ticker", forbidden)
    monkeypatch.setattr(agent_tools, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(chat_tools, "REPORT_DIR", tmp_path)
    memory = SimpleNamespace(history=[], saved=[], reads=0)

    def history(ticker, n=1):
        memory.reads += 1
        return deepcopy(memory.history)

    def save(**kwargs):
        memory.saved.append(deepcopy(kwargs))
        return len(memory.saved)

    monkeypatch.setitem(sys.modules, "bellomberg.storage.memory_db", SimpleNamespace(MemoryDB=lambda:
        SimpleNamespace(get_valuation_history=history, save_valuation_thesis=save)))
    return SimpleNamespace(chat=chat_tools, build=agent_tools.tool_build_dcf_model,
                           engine=dcf_engine, memory=memory, directory=tmp_path)


def seed_cache(tools, bundle):
    payload = tools.engine.generate_valuation(SYMBOL, prepared_bundle=bundle,
                                               output_dir=str(tools.directory))
    assert payload["valuation_usability"]["usable"], payload["valuation_usability"]
    path = Path(payload["path"])
    sidecar = path.with_suffix(".payload.json")
    persisted = json.loads(sidecar.read_text(encoding="utf-8"))
    # The returned envelope adds presentation fields; the persisted proof is native.
    for key in ("valuation_decision", "snapshot_id", "generation_id", "acquisition_snapshot",
                "workbook_sha256", "sanity", "analytical_quality", "input_consumption",
                "fair_value_base"):
        assert persisted[key] == payload[key], key
    tools.memory.history = [{"valuation_payload": deepcopy(payload), "generation_id": payload["generation_id"]}]
    return payload, path, sidecar




# ---------------------------------------------------------------------------------------------
# Contratto ATTUALE (ZR 05/10, Z1). I 6 test chat/build/cache sono in
# archive/private/attic/tests_excel_archiviato_20261005/test_sector_valuation_integration_legacy.py: dal commit 1326312
# dispatch('get_valuation') risponde excel_archived prima del ramo che li serviva (censimento Z4:
# ramo chat_tools e tool_build_dcf_model SOLO ARCHIVIATI). Le fixture/helper sopra restano:
# test_documented_consumers, test_managed_care_integration e test_price_divergence_consumers le importano.
# ---------------------------------------------------------------------------------------------
from _contratto_excel_archiviato import blinda_ramo_archiviato, file_in, verifica_archiviato


@pytest.mark.parametrize("variante", ["software", "bank", "cef", "etf", "incompleto", "variant_view",
                                      "cache_presente"])
def test_get_valuation_meets_the_archived_contract_without_cache_or_memory(isolated_tools, monkeypatch, variante):
    tools = isolated_tools
    model = variante if variante in ("software", "bank", "cef", "etf") else "software"
    bundle = synthetic_bundle(model=model, complete=variante != "incompleto")
    before = deepcopy(bundle)
    tool_input = {"ticker": SYMBOL}
    if variante == "variant_view":
        tool_input["variant_view"] = "new synthetic analyst view"
    if variante == "cache_presente":
        # Una cache "valida" in memoria non deve essere ne' letta ne' restituita come dato.
        tools.memory.history = [{"fair_value": 999, "sanity_severity": "OK",
                                 "valuation_payload": {"snapshot_id": bundle["snapshot_id"]}}]
    prima = file_in(tools.directory)
    chiamate = blinda_ramo_archiviato(monkeypatch)
    risposta = tools.chat.dispatch("get_valuation", tool_input, prepared_bundle=bundle)
    verifica_archiviato(risposta, chiamate, cartella=tools.directory, prima=prima)
    assert tools.memory.reads == 0 and tools.memory.saved == []
    assert bundle == before
