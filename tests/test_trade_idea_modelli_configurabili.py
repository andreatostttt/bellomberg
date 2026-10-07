"""MOD-TI (06/10, Opus 5.5) — DECISIONE PM: la Trade Idea sceglie i modelli dal .env come la
run settimanale (TRADE_IDEA_{SPECIALIST,RED_TEAM,CAPO,AUX}_MODEL; assente/vuota = default
Muse/Opus di oggi). La scelta si CONGELA nel contratto della run all'accettazione: una
ripresa usa sempre i modelli del contratto, e un .env cambiato dopo e' DICHIARATO, non applicato.

Nessuna chiamata AI vera: client e catalogo finti. Le capacita' dei modelli alternativi
(effort ammessi, tetti) sono quelle lette dalla Models API OpenRouter il 06/10 (GET gratuito):
  google/gemini-3.8-flash          efforts high/medium/low      max_completion 65536
  deepseek/deepseek-v4-flash-0731  efforts max/high/low         max_completion 943718
  z-ai/glm-5.3-flash               efforts max/high/low         max_completion 943717
"""
from copy import deepcopy
import io
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V4
from test_trade_idea_no_workbook_e2e import no_workbook_case, _assert_complete, _provider_round  # noqa: F401
from _smtp_cattura import smtp  # noqa: F401
from test_trade_idea_source_research import frozen_clock  # noqa: F401
from test_trade_idea_store import db_path, migrated, store  # noqa: F401
from test_trade_idea_pipeline import _priced_request, FakeMessages, _gate

ROLES = ("specialist", "red_team", "capo", "aux")
VARS = {"specialist": "TRADE_IDEA_SPECIALIST_MODEL", "red_team": "TRADE_IDEA_RED_TEAM_MODEL",
        "capo": "TRADE_IDEA_CAPO_MODEL", "aux": "TRADE_IDEA_AUX_MODEL"}
DEFAULTS = {"specialist": "meta/muse-spark-1.3", "red_team": "meta/muse-spark-1.3",
            "capo": "anthropic/claude-opus-5.5", "aux": "meta/muse-spark-1.3"}
# Scelta alternativa usata dal flusso completo: ogni effort della policy storica (max) e'
# ammesso dalla Models API per questi slug, e il loro tetto supera i 128000 richiesti.
ALT = {"specialist": "deepseek/deepseek-v4-flash-0731", "red_team": "deepseek/deepseek-v4-flash-0731",
       "capo": "z-ai/glm-5.3-flash", "aux": "deepseek/deepseek-v4-flash-0731"}
GEMINI = {role: "google/gemini-3.8-flash" for role in ROLES}
API = {  # misurato 06/10 sulla Models API
    "meta/muse-spark-1.3": (["max", "xhigh", "high", "medium", "low", "minimal"], 1048576, 943718),
    "anthropic/claude-opus-5.5": (["max", "xhigh", "high", "medium", "low"], 1000000, 128000),
    "google/gemini-3.8-flash": (["high", "medium", "low"], 1048576, 65536),
    "deepseek/deepseek-v4-flash-0731": (["max", "high", "low"], 1048576, 943718),
    "z-ai/glm-5.3-flash": (["max", "high", "low"], 1048576, 943717),
}


def _set_env(monkeypatch, models):
    for role in ROLES:
        if models is None:
            monkeypatch.setenv(VARS[role], "")
        else:
            monkeypatch.setenv(VARS[role], models[role])


def _api_rows(slugs):
    rows = []
    for slug in sorted(set(slugs)):
        efforts, context, max_completion = API[slug]
        rows.append({"id": slug, "context_length": context,
                     "top_provider": {"max_completion_tokens": max_completion},
                     "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                     "supported_parameters": ["reasoning", "reasoning_effort", "max_tokens", "tools",
                                              "tool_choice", "response_format", "structured_outputs"],
                     "reasoning": {"supported_efforts": efforts}})
    return rows


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _live_catalog(seen):
    def opener(request, timeout):
        seen.append(request.full_url)
        return _Resp(json.dumps({"data": _api_rows(API)}).encode())
    return lambda **kw: trade_idea.fetch_model_catalog(opener=opener, use_cache=False, **kw)


def _preflight(fetcher, policy=None):
    return trade_idea.preflight_trade_idea(
        "ZZTEST", "Tesi sintetica", "manual", "10", catalog_fetcher=fetcher,
        identity_resolver=lambda ticker: {"ticker": ticker, "status": "invalid", "reason": "offline test"},
        key_checker=lambda: None, mandate_loader=lambda: {}, active_checker=lambda: False,
        **({"analysis_mode": "fundamentals_research_v1", "execution_policy": policy} if policy else {}))


# ---------------------------------------------------------------- scelta dal .env (preflight)
@pytest.mark.parametrize("chosen", [ALT, GEMINI], ids=["deepseek_glm", "gemini"])
def test_preflight_legge_i_modelli_dal_env(monkeypatch, chosen):
    _set_env(monkeypatch, chosen)
    checked = _preflight(_live_catalog([]))
    reasons = " | ".join(checked["reasons"])
    assert "catalogo modelli non verificabile" not in reasons, reasons
    assert {row["role"]: row["model"] for row in checked["models"]} == chosen
    assert {role: row["id"] for role, row in checked["catalog_snapshot"]["models"].items()} == chosen


def test_env_assente_o_vuoto_usa_i_default_di_oggi(monkeypatch):
    for role in ROLES:
        monkeypatch.delenv(VARS[role], raising=False)
    assert trade_idea.configured_models() == DEFAULTS
    _set_env(monkeypatch, None)
    checked = _preflight(_live_catalog([]))
    assert {row["role"]: row["model"] for row in checked["models"]} == DEFAULTS


def test_slug_non_valido_e_dichiarato_col_nome_della_variabile(monkeypatch):
    _set_env(monkeypatch, None)
    monkeypatch.setenv("TRADE_IDEA_CAPO_MODEL", "gemini flash")
    checked = _preflight(_live_catalog([]))
    reasons = " | ".join(checked["reasons"])
    assert checked["ok"] is False and checked["models"] == []
    assert ("TRADE_IDEA_CAPO_MODEL='gemini flash' (ruolo Capo): non e' uno slug OpenRouter valido"
            in reasons), reasons


def test_effort_della_policy_non_ammesso_dal_modello_e_dichiarato_prima_di_spendere(monkeypatch):
    # V4: effort medium per ruolo. DeepSeek V4 Flash ammette max/high/low (Models API): rifiuto
    # dichiarato con variabile, ruolo, effort ammessi e cosa fare; mai un effort sostituito.
    # R-MOD punto 4: TUTTI i ruoli che non vanno, non solo il primo; niente «catalogo non verificabile».
    _set_env(monkeypatch, {**DEFAULTS, "specialist": "deepseek/deepseek-v4-flash-0731",
                           "aux": "deepseek/deepseek-v4-flash-0731"})
    checked = _preflight(_live_catalog([]), EXECUTION_POLICY_V4)
    reasons = " | ".join(checked["reasons"])
    assert "catalogo modelli non verificabile" not in reasons, reasons
    for variable, label in (("TRADE_IDEA_SPECIALIST_MODEL", "desk specialisti"), ("TRADE_IDEA_AUX_MODEL", "ausiliario")):
        assert ("modello non utilizzabile: " + variable + "=deepseek/deepseek-v4-flash-0731 (ruolo " + label
                + "): la policy della run chiede effort 'medium' (lo fissa la policy, non si configura); "
                "questo modello accetta solo max, high, low. Scegli per questo ruolo un modello che "
                "accetti 'medium' (es. il default meta/muse-spark-1.3).") in checked["reasons"], reasons
    assert "TRADE_IDEA_CAPO_MODEL" not in reasons and "TRADE_IDEA_RED_TEAM_MODEL" not in reasons


def test_variante_exacto_rifiutata_con_messaggio_chiaro_e_tutti_i_ruoli(monkeypatch):
    # Regex e catalogo coerenti: :exacto non e' una riga del catalogo e il fornitore risponde col
    # nome base (il controllo d'identita' della risposta lo rifiuterebbe): rifiutata subito, chiara.
    _set_env(monkeypatch, {**DEFAULTS, "red_team": "deepseek/deepseek-v4-flash-0731:exacto",
                           "capo": "slug sbagliato"})
    asked = []
    checked = _preflight(_live_catalog(asked), EXECUTION_POLICY_V4)
    reasons = " | ".join(checked["reasons"])
    assert ("TRADE_IDEA_RED_TEAM_MODEL=deepseek/deepseek-v4-flash-0731:exacto (ruolo Red Team): la variante "
            "di instradamento :exacto non e' ammessa nella Trade Idea") in reasons, reasons
    assert "Usa lo slug base deepseek/deepseek-v4-flash-0731." in reasons
    assert "TRADE_IDEA_CAPO_MODEL='slug sbagliato' (ruolo Capo)" in reasons
    assert "catalogo modelli non verificabile" not in reasons and asked == []


def test_modello_assente_dal_catalogo_nomina_la_variabile(monkeypatch):
    _set_env(monkeypatch, {**DEFAULTS, "capo": "acme/modello-inesistente-zz"})
    checked = _preflight(_live_catalog([]), EXECUTION_POLICY_V4)
    assert ("modello non utilizzabile: TRADE_IDEA_CAPO_MODEL=acme/modello-inesistente-zz (ruolo Capo): "
            "modello assente dal catalogo OpenRouter (openrouter.ai/models). Controlla lo slug o scegli "
            "un altro modello per questo ruolo.") in checked["reasons"], checked["reasons"]


def test_api_avvio_modello_inadatto_e_428_non_409():
    # R-MOD punto 4: prima bastava «Trade Idea» nel testo per un 409 (conflitto); un modello
    # del .env inadatto e' una precondizione da correggere: 428. Funzione pura, niente TestClient.
    from bellomberg.api.trade_idea_routes import start_refusal_code
    model = ["modello non utilizzabile: TRADE_IDEA_RED_TEAM_MODEL=x/y:exacto (ruolo Red Team): la variante "
             "di instradamento :exacto non e' ammessa nella Trade Idea"]
    assert start_refusal_code(model) == 428
    assert start_refusal_code(["limite di spesa Trade Idea mancante"]) == 428
    assert start_refusal_code(model + ["un'altra run pagata e' attiva"]) == 409
    assert start_refusal_code(["Trade Idea gia' accettata o in esecuzione"]) == 409


def test_glm_effort_non_inviato_e_dichiarato_non_bloccato(monkeypatch):
    # z-ai/: llm_client omette l'effort (ragionamento nativo): il catalogo non lo vincola.
    _set_env(monkeypatch, {**DEFAULTS, "capo": "z-ai/glm-5.3-flash"})
    checked = _preflight(_live_catalog([]), EXECUTION_POLICY_V4)
    reasons = " | ".join(checked["reasons"])
    assert "Effort richiesto" not in reasons, reasons
    capo = next(row for row in checked["models"] if row["role"] == "capo")
    assert capo["model"] == "z-ai/glm-5.3-flash"
    assert capo["reason"] == "effort non inviato: ragionamento nativo del fornitore"


# ---------------------------------------------------------------- guardie sul contratto
def _alt_payload():
    payload = _priced_request()
    for role in ROLES:
        payload["models"][role]["model"] = ALT[role]
        payload["catalog_snapshot"]["models"][role]["id"] = ALT[role]
    return payload


def _call(model):
    return {"model": model, "max_tokens": 100, "thinking": {"type": "effort", "effort": "max"},
            "messages": [{"role": "user", "content": "Offline provider test"}]}


def test_gate_accetta_il_modello_del_contratto_e_rifiuta_quello_del_env_di_oggi(migrated, monkeypatch):
    s = store(migrated)
    _set_env(monkeypatch, ALT)
    payload = _alt_payload()
    run_id = s.create_run(payload, idempotency_key="contratto-alt")["run"]["id"]
    s.claim_run(run_id)
    _set_env(monkeypatch, DEFAULTS)  # .env cambiato DOPO l'accettazione
    gate = _gate(s, run_id, payload)
    assert gate.contract_models == ALT
    assert trade_idea.model_for_role("specialist", gate) == ALT["specialist"]
    assert trade_idea.model_for_role("specialist") == DEFAULTS["specialist"]
    fake = FakeMessages("0.0001")
    client = gate.wrap_client(SimpleNamespace(messages=fake), role="specialist:macro")
    client.messages.create(**_call(ALT["specialist"]))
    with pytest.raises(ValueError, match="fuori dal contratto della run"):
        client.messages.create(**_call(DEFAULTS["specialist"]))
    assert fake.calls == 1
    notice = gate.declare_model_selection()
    assert notice["status"] == "env_differs"
    assert {row["variable"] for row in notice["drift"]} == set(VARS.values())
    assert "TRADE_IDEA_CAPO_MODEL=anthropic/claude-opus-5.5 non applicato, contratto z-ai/glm-5.3-flash" in notice["notice"]


def test_catalogo_live_del_gate_chiede_i_modelli_del_contratto(migrated, monkeypatch):
    s = store(migrated)
    payload = _alt_payload()
    run_id = s.create_run(payload, idempotency_key="catalogo-contratto")["run"]["id"]
    token = s.claim_run(run_id)
    _set_env(monkeypatch, GEMINI)
    asked = []

    def fetch(**kw):
        asked.append(kw.get("models"))
        return deepcopy(payload["catalog_snapshot"])
    monkeypatch.setattr(trade_idea, "fetch_model_catalog", fetch)
    gate = trade_idea.TradeIdeaBudgetGate(s, run_id, token, payload["catalog_snapshot"])
    gate.wrap_client(SimpleNamespace(messages=FakeMessages("0.0001")),
                     role="specialist:macro").messages.create(**_call(ALT["specialist"]))
    assert asked == [ALT]


def test_recupero_risposta_attesta_il_modello_del_contratto_non_del_env(monkeypatch):
    # Guardia anti-manomissione del recupero report (validate_response_recovery): il modello
    # atteso e' quello della run accettata, anche se oggi il .env ne sceglie un altro.
    from bellomberg.core.llm_client import costruisci_corpo
    catalog = deepcopy(_alt_payload()["catalog_snapshot"])
    pricing = catalog["models"]["specialist"]["pricing"]
    price_cap = {"prompt": float(trade_idea.Decimal(pricing["prompt"]) * 10**6),
                 "completion": float(trade_idea.Decimal(pricing["completion"]) * 10**6), "request": 0.0}

    def kwargs(model):
        return {"model": model, "max_tokens": 64000, "thinking": {"type": "effort", "effort": "max"},
                "tool_choice": {"type": "none"},
                "messages": [{"role": "user", "content": "Recupero sintetico ZZTEST"}]}

    def descriptor(model):
        return {"mode": "report_only", "desk": "macro", "replacement_max_tokens": 128000,
                "request_sha256": trade_idea._plan_digest(costruisci_corpo(
                    **{**kwargs(model), "provider_max_price": price_cap}))}

    def gate_for(accepted):
        gate = trade_idea.TradeIdeaBudgetGate.__new__(trade_idea.TradeIdeaBudgetGate)
        gate.run_id, gate.catalog_snapshot = "run-sintetica", catalog
        gate.store = SimpleNamespace(get_run=lambda _id: {"run": {"continuation": {
            "specialist_response_recovery": accepted}}})
        return gate

    _set_env(monkeypatch, DEFAULTS)
    accepted = descriptor(ALT["specialist"])
    gate_for(accepted).validate_response_recovery(accepted, kwargs(ALT["specialist"]), role="specialist:macro")
    forged = descriptor(DEFAULTS["specialist"])
    with pytest.raises(ValueError, match="report completion differs"):
        gate_for(forged).validate_response_recovery(forged, kwargs(DEFAULTS["specialist"]),
                                                    role="specialist:macro")


def test_contratto_incompleto_e_un_errore_non_un_default():
    with pytest.raises(ValueError, match="contratto modelli Trade Idea"):
        trade_idea.model_for_role("capo", {"models": {"specialist": {"model": "x/y"}}})


# ---------------------------------------------------------------- flusso completo fino al PDF
def _use_models(case, models):
    for role in ROLES:
        case.request["models"][role]["model"] = models[role]
        row = case.request["catalog_snapshot"]["models"][role]
        efforts, context, max_completion = API[models[role]]
        row.update(id=models[role], supported_efforts=list(efforts), context_length=context,
                   max_completion_tokens=max_completion)


def _call_role(call):
    if (call.get("response_format") or {}).get("type") == "json_object":
        return "capo"
    if (call.get("response_format") or {}).get("json_schema", {}).get("name") == "trade_idea_committee_review":
        return "red_team"
    return "specialist" if _provider_round(call) is not None else "aux"


def _assert_models(calls, models):
    seen = set()
    for call in calls:
        role = _call_role(call)
        seen.add(role)
        assert call["model"] == models[role], (role, call["model"])
    assert {"specialist", "red_team", "capo"} <= seen, seen


def test_run_con_modelli_alternativi_arriva_al_pdf(no_workbook_case, monkeypatch):
    case = no_workbook_case
    _set_env(monkeypatch, ALT)
    assert trade_idea.configured_models() == ALT
    _use_models(case, ALT)
    case.state.update(judgment="favorable", source_gaps=False)
    ident = case.current.create_run(case.request, idempotency_key="modelli-alt")["run"]["id"]
    detail = case.execute(ident, "modelli-alt")
    _assert_complete(case, detail, judgment="favorable", destination="dcn")
    _assert_models(case.providers, ALT)
    assert detail["progress"]["model_contract"]["status"] == "env_matches"
    assert detail["progress"]["model_contract"]["models"] == ALT


def test_env_cambiato_fra_accettazione_e_ripresa_usa_il_contratto_e_lo_dichiara(no_workbook_case, monkeypatch):
    case = no_workbook_case
    _set_env(monkeypatch, ALT)
    _use_models(case, ALT)
    current = case.current
    parent = current.create_run(case.request, idempotency_key="modelli-ripresa")["run"]["id"]
    case.state["stop_after"] = 0
    first = case.execute(parent, "ripresa-0")
    assert first["run"]["technical_status"] == "incomplete", first["run"]["reason"]
    before = len(case.providers)
    _set_env(monkeypatch, GEMINI)  # il PM cambia il .env a run accettata
    case.state["stop_after"] = None
    child = current.create_continuation(parent, idempotency_key="modelli-ripresa-1",
                                        authorize_new_requests=True)["run"]["id"]
    detail = case.execute(child, "ripresa-1")
    _assert_complete(case, detail, judgment="rejected", destination="research")
    assert len(case.providers) > before
    _assert_models(case.providers, ALT)
    assert not any(call["model"] == GEMINI["specialist"] for call in case.providers)
    contract = detail["progress"]["model_contract"]
    assert contract["status"] == "env_differs" and contract["models"] == ALT
    assert {row["variable"]: row["env_model"] for row in contract["drift"]} == {
        VARS[role]: GEMINI[role] for role in ROLES}
    assert "TRADE_IDEA_SPECIALIST_MODEL=google/gemini-3.8-flash non applicato" in contract["notice"]


def test_run_tutta_gemini_flash_tetto_65536_arriva_al_pdf(no_workbook_case, monkeypatch, capsys):
    """MOD-CAP (06/10, decisione PM «di default 128.000, ma se lancio un modello con un tetto minore
    si adatta in automatico»): gemini-3.8-flash ha tetto di uscita 65536 nella Models API. Prima
    ogni chiamata a 128000 era rifiutata dal gate («max_tokens incompatibile») e il preflight
    bloccava il Capo; ora il gate adatta al tetto del CONTRATTO, dichiarato, e la run arriva al PDF."""
    from bellomberg.core import llm_client
    monkeypatch.setattr(llm_client, "_TETTI_DICHIARATI", set())
    case = no_workbook_case
    _set_env(monkeypatch, GEMINI)
    _use_models(case, GEMINI)
    case.state.update(judgment="favorable", source_gaps=False)
    ident = case.current.create_run(case.request, idempotency_key="modelli-gemini")["run"]["id"]
    detail = case.execute(ident, "modelli-gemini")
    _assert_complete(case, detail, judgment="favorable", destination="dcn")
    _assert_models(case.providers, GEMINI)
    caps = [call["max_tokens"] for call in case.providers]
    assert caps and max(caps) == 65536 and 128000 not in caps
    out = capsys.readouterr().out
    assert "trade_idea:capo: richiesti 128000 token, google/gemini-3.8-flash ne accetta 65536: uso 65536" in out


def test_preparer_trade_idea_al_tetto_del_contratto(tmp_path, monkeypatch):
    """MOD-CAP: il preparer della Trade Idea (contabilita' propria, BudgetedProposer) chiede 128000
    di default; col modello aux del contratto a 65536 parte a 65536, prima della sua chiave."""
    from bellomberg.core import llm_client
    monkeypatch.setattr(llm_client, "_TETTI_DICHIARATI", set())
    snapshot = deepcopy(_priced_request()["catalog_snapshot"])
    snapshot["models"]["aux"]["max_completion_tokens"] = 65536
    run = {"ticker": "ZZTEST", "budget_limit_usd": "1",
           "authorization": {"activities": ["model_preparation"]}}
    g = object.__new__(trade_idea.TradeIdeaBudgetGate)
    g.store = SimpleNamespace(get_run=lambda ident: {"run": run})
    g.run_id, g.catalog_snapshot = "zz-run", snapshot
    proposer, stato = trade_idea.bind_trade_idea_preparer(g, "ZZTEST", output_dir=tmp_path)
    assert stato["status"] == "enabled" and proposer.max_tokens == 65536
    snapshot["models"]["aux"]["max_completion_tokens"] = 943718
    proposer, _ = trade_idea.bind_trade_idea_preparer(g, "ZZTEST", output_dir=tmp_path / "b")
    assert proposer.max_tokens == 128000


def _gate_recupero(tetto_specialist, recovery):
    snapshot = deepcopy(_priced_request()["catalog_snapshot"])
    snapshot["models"]["specialist"]["max_completion_tokens"] = tetto_specialist
    g = object.__new__(trade_idea.TradeIdeaBudgetGate)
    g.store = SimpleNamespace(get_run=lambda ident: {"run": {"continuation": {
        "specialist_response_recovery": recovery}}})
    g.run_id, g.catalog_snapshot = "zz-run", snapshot
    return g, snapshot


def _sha_inviato(snapshot, kwargs):
    from bellomberg.core.llm_client import costruisci_corpo
    from decimal import Decimal
    pricing = snapshot["models"]["specialist"]["pricing"]
    return trade_idea._plan_digest(costruisci_corpo(**{**kwargs, "provider_max_price": {
        "prompt": float(Decimal(pricing["prompt"]) * 10**6),
        "completion": float(Decimal(pricing["completion"]) * 10**6), "request": 0.0}}))


def test_recupero_report_al_cap_adattato_riconosciuto(monkeypatch):
    """R-MOD F3: la richiesta originale e' partita al cap ADATTATO (65536 -> 32768 col modello del
    contratto): il recupero autorizzato la riconosce dallo sha di cio' che e' stato inviato."""
    from bellomberg.core import llm_client
    monkeypatch.setattr(llm_client, "_TETTI_DICHIARATI", set())
    descriptor = {"mode": "report_only", "desk": "quant", "replacement_max_tokens": 128000}
    g, snapshot = _gate_recupero(32768, descriptor)
    kwargs = {"model": snapshot["models"]["specialist"]["id"], "max_tokens": 65536,
              "thinking": {"type": "effort", "effort": "max"}, "system": "S", "tools": [],
              "messages": [{"role": "user", "content": "ZZTEST report"}], "tool_choice": {"type": "none"}}
    descriptor["request_sha256"] = _sha_inviato(snapshot, {**kwargs, "max_tokens": 32768})
    g.validate_response_recovery(descriptor, kwargs, role="specialist:quant")
    descriptor["request_sha256"] = _sha_inviato(snapshot, {**kwargs, "max_tokens": 20000})
    with pytest.raises(ValueError, match="wire contract differs"):
        g.validate_response_recovery(descriptor, kwargs, role="specialist:quant")


def test_recupero_autore_fallito_al_cap_adattato_riconosciuto(monkeypatch):
    from bellomberg.core import llm_client
    monkeypatch.setattr(llm_client, "_TETTI_DICHIARATI", set())
    descriptor = {"version": 1, "kind": "failed_model_authoring", "mode": "model_authoring_error_retry",
                  "desk": "fundamentals", "round_n": 1, "original_max_tokens": 128000,
                  "replacement_max_tokens": 128000, "max_tool_iters": 30, "original_iteration": 3}
    g, snapshot = _gate_recupero(65536, descriptor)
    g.blackboard = SimpleNamespace(run_scope="trade_idea", model_phase="building", current_round=1, data={
        "_model_authoring_completion": {"version": 1, "mode": "model_authoring_completion"},
        "_model_authoring_context_projections": []})
    tools = [{"name": "zz_tool", "description": "sintetico", "input_schema": {"type": "object", "properties": {}}}]
    kwargs = {"model": snapshot["models"]["specialist"]["id"], "max_tokens": 128000,
              "thinking": {"type": "effort", "effort": "max"}, "system": "S", "tools": tools,
              "messages": [{"role": "user", "content": "ZZTEST autore"}]}
    descriptor["request_sha256"] = _sha_inviato(snapshot, {**kwargs, "max_tokens": 65536})
    g.validate_response_recovery(descriptor, kwargs, role="specialist:fundamentals")
    descriptor["request_sha256"] = _sha_inviato(snapshot, {**kwargs, "max_tokens": 50000})
    with pytest.raises(ValueError, match="failed author request"):
        g.validate_response_recovery(descriptor, kwargs, role="specialist:fundamentals")
