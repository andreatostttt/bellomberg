"""Tetto di output degli specialisti 128k (decisione PM 02/10), il timeout
del client che lo segue, e la troncatura che si DIAGNOSTICA dal log.

I fatti (run V8 e V9): quant R2 troncata due volte su due con
`stop_reason=max_tokens`. In V9 il log aggrega `out=14.512` su 3 call e il
testo salvato era 9.578 char: a 2,1 char/token (misure di casa, `count_tokens`
del 26/07) ≈ 4,6k token di testo, quindi circa 2/3 del cap era ragionamento
adattivo — che conta nel `max_tokens` («hard cap on thinking plus response
text», skill claude-api, migrazione a Opus 5). Il WARN diceva solo «TRONCATA».

Review 27/08 (1 agente Fable): (1) alzare il cap del 33% senza toccare il
`timeout=240` del client (#196, anti-blocco) esponeva a un timeout + retry
che perde il report INTERO — la call da 12k in V9 e' durata <=203 s (65-80
tok/s), a 16k sono 200-250 s; l'SDK stesso stima per il non-streaming
3600 x max_tokens / 128000 = 450 s a 16k → il timeout segue il cap;
(2) i test asserivano `== 16000`, che non distingue la costante da un
letterale → sentinella sulla costante.

Client finto scriptabile (idioma di test_run_robustness), zero rete, heartbeat
su tmp.
"""
import re
import sys
import types

import pytest

from bellomberg.core import llm_pricing
import bellomberg.agents.specialists.base as base
from bellomberg.agents.specialists.base import Blackboard, Specialist


class _Usage:
    def __init__(self, out=50):
        self.input_tokens = 100
        self.output_tokens = out
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cost_usd = 0.03  # Explicit simulated provider receipt; never a price estimate.


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, stop_reason, content, usage=None):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = usage


class _FakeClient:
    def __init__(self, script):
        self.calls = []
        self._script = script
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._script(len(self.calls), kwargs)


class _MockSpecialist(Specialist):
    name = "quant"
    role = "mock"
    system_prompt = "Sei un mock per il collaudo offline."
    tools_used = []


@pytest.fixture
def bb(tmp_path, monkeypatch):
    # heartbeat su tmp: `data/current_run.json` vero e' della UI live (F4)
    monkeypatch.setattr(Blackboard, "HEARTBEAT_PATH", str(tmp_path / "current_run.json"))
    # FX deterministico: record_usage -> cost_eur non deve toccare price_updater
    monkeypatch.setattr(llm_pricing, "_resolve_fx_usd_to_eur", lambda: (0.9, "fallback"))
    llm_pricing.reset_fx_memo()
    # current_facts legge il DB vivo: stub (qui si collauda il loop, non i fatti)
    cf = types.ModuleType("current_facts")
    cf.current_facts_block = lambda: ""
    cf.favorites_block = lambda: ""
    cf.pm_theses_block = lambda: ""
    cf.research_block = lambda: ""
    monkeypatch.setitem(sys.modules, 'bellomberg.core.current_facts', cf)
    import bellomberg.core
    monkeypatch.setattr(bellomberg.core, "current_facts", cf, raising=False)
    # chat_tools: registro finto (il dispatcher vero chiamerebbe tool con rete)
    ct = types.ModuleType("chat_tools")
    _tool = {"name": "get_portfolio_live", "description": "mock",
             "input_schema": {"type": "object", "properties": {}}}
    ct.get_tools_for_agent = lambda name: [dict(_tool)]
    ct.TOOL_DEFINITIONS = [dict(_tool)]
    ct.dispatch = lambda name, args=None: {"ok": True, "mock": name}
    monkeypatch.setitem(sys.modules, 'bellomberg.agents.chat_tools', ct)
    import bellomberg.agents
    monkeypatch.setattr(bellomberg.agents, "chat_tools", ct, raising=False)
    board = Blackboard()
    yield board
    llm_pricing.reset_fx_memo()


def _riga_warn(out):
    righe = [r for r in out.splitlines() if "TRONCATA" in r and "[quant]" in r]
    assert len(righe) == 1, "attesa UNA riga WARN TRONCATA del quant, trovate: %r" % (out,)
    return righe[0]


# ------------------------------------------------------------------ il cap

@pytest.mark.parametrize("scope", ["weekly", "trade_idea"])
@pytest.mark.parametrize("desk", ["macro", "eventdesk", "crypto", "fundamentals", "quant", "options"])
@pytest.mark.parametrize("round_n,task", [(0, None), (1, None),
    (1, {"consultation": True, "question": "Complete the supplied synthetic analysis."}), (2, None)])
def test_all_native_specialist_phases_send_128k_without_changing_model_or_effort(
        bb, monkeypatch, scope, desk, round_n, task):
    """Catch a desk/round/consultation retaining the former output ceiling."""
    bb.run_scope, bb.model_phase, bb.target_ticker = scope, "building", "SYNTH"
    bb.source_qualification = {}
    bb.budget_gate = types.SimpleNamespace(wrap_client=lambda client, **kwargs: client)
    report = "Complete synthetic report with remaining evidence gaps explicitly declared. " * 30
    client = _FakeClient(lambda n, kw: _Resp("end_turn", [_TextBlock(report)], _Usage()))
    actor_type = type("Policy" + desk, (_MockSpecialist,), {"name": desk})
    actor = actor_type(bb, client=client)
    monkeypatch.setattr(actor, "_build_round_context", lambda _: "Frozen offline analyst context.")
    expected_model = actor._model_for_round(round_n)
    expected_effort = ({"type": "effort", "effort": "max"} if scope == "trade_idea"
                       else base.thinking_consigliere(expected_model))

    result = actor.run(round_n, task_context=task, publish_report=task is None)

    assert len(client.calls) == 1
    assert client.calls[0]["max_tokens"] == 128000
    assert client.calls[0]["model"] == expected_model
    assert client.calls[0]["thinking"] == expected_effort
    assert "tool_choice" not in client.calls[0]
    assert actor.run_result_status == "complete" and report in result


def test_fundamentals_analysis_has_room_for_reasoning_and_statements(bb, monkeypatch):
    """10/09 real Fundamentals R1/R2 used all 16k with no visible report."""
    specialist = _MockSpecialist(bb, client=object())
    specialist.name = 'fundamentals'
    assert specialist._max_tokens_for_round(0) == base.MAX_TOKENS_SPECIALIST
    assert specialist._max_tokens_for_round(1) == 128000
    assert specialist._max_tokens_for_round(2) == 128000
    monkeypatch.setattr(base, 'MAX_TOKENS_FUNDAMENTALS_ANALYSIS', 32001)
    assert specialist._max_tokens_for_round(1) == 32001
    specialist.name = 'quant'
    assert specialist._max_tokens_for_round(1) == base.MAX_TOKENS_SPECIALIST


def test_weekly_fundamentals_owned_http_timeout_follows_round_cap_and_resets(bb, monkeypatch):
    import httpx
    client = _FakeClient(lambda n, kw: _Resp('end_turn', [_TextBlock('Completed synthetic report. ' * 30)], _Usage()))
    client._http = types.SimpleNamespace(timeout=httpx.Timeout(base.TIMEOUT_SPECIALIST_S, connect=10))
    client.timeout = base.TIMEOUT_SPECIALIST_S
    monkeypatch.setattr(base, 'OpenRouterClient', lambda **kw: client)
    specialist = _MockSpecialist(bb)
    specialist.name = 'fundamentals'
    specialist.run(1)
    assert client.calls[0]['max_tokens'] == 128000
    assert client.timeout == 3600.0
    assert client._http.timeout.read == 3600.0
    assert client._http.timeout.connect == 10
    specialist.run(0)
    assert client.calls[-1]['max_tokens'] == base.MAX_TOKENS_SPECIALIST
    assert client.timeout == base.TIMEOUT_SPECIALIST_S and client._http.timeout.connect == 10


@pytest.mark.parametrize("desk", ["macro", "crypto"])
def test_explicit_legacy_r2_keeps_65536_and_its_effective_timeout(bb, monkeypatch, desk):
    """A larger default must not silently expand an accepted private request."""
    import httpx
    from bellomberg.agents import trade_idea

    ref = {"snapshot_id": "frozen-snapshot", "generation_id": "frozen-generation",
           "workbook_sha256": "a" * 64}
    bb.run_scope, bb.model_phase, bb.target_ticker = "trade_idea", "review", "SYNTH"
    bb.source_qualification = {}
    bb.valuation_results["SYNTH"] = dict(ref)
    bb._trade_idea_r2_completion_limits = {"scope": "trade_idea_r2_final_completion_v1",
        "round": 2, "model_ref": dict(ref), "max_tokens_by_desk": {"macro": 65536, "crypto": 65536}}
    monkeypatch.setattr(trade_idea, "_verified_candidate_valuations", lambda board: [dict(ref)])
    bb.budget_gate = types.SimpleNamespace(wrap_client=lambda client, **kwargs: client)
    client = _FakeClient(lambda n, kw: _Resp("end_turn",
        [_TextBlock("Complete frozen-model review with all evidence gaps declared. " * 30)], _Usage()))
    client._http = types.SimpleNamespace(timeout=httpx.Timeout(base.TIMEOUT_SPECIALIST_S, connect=10))
    client.timeout = base.TIMEOUT_SPECIALIST_S
    monkeypatch.setattr(base, "OpenRouterClient", lambda **kwargs: client)
    actor = type("Legacy" + desk, (_MockSpecialist,), {"name": desk})(bb)
    monkeypatch.setattr(actor, "_build_round_context", lambda _: "Frozen exact-model context.")

    actor.run(2, task_context={"purpose": "final_completion"})

    assert len(client.calls) == 1 and client.calls[0]["max_tokens"] == 65536
    assert client.calls[0]["thinking"] == {"type": "effort", "effort": "max"}
    assert client.timeout == 1843.2
    assert all(getattr(client._http.timeout, key) == 1843.2 for key in ("read", "write", "pool"))
    assert client._http.timeout.connect == 10
    del bb._trade_idea_r2_completion_limits
    actor.run(0)
    assert len(client.calls) == 2 and client.calls[-1]["max_tokens"] == 128000
    assert client.timeout == 3600.0 and client._http.timeout.read == 3600.0
    assert client._http.timeout.connect == 10


def test_la_chiamata_api_segue_la_costante_non_un_letterale(bb, monkeypatch):
    """Il cablaggio: con la costante a una sentinella, la call la segue. Un
    `max_tokens=16000` scritto a mano passerebbe il test sul valore, non questo."""
    monkeypatch.setattr(base, "MAX_TOKENS_SPECIALIST", 4242)
    client = _FakeClient(lambda n, kw: _Resp("end_turn", [_TextBlock("REPORT")], _Usage()))

    _MockSpecialist(bb, client=client).run(1)

    assert client.calls[0]["max_tokens"] == 4242, client.calls[0]["max_tokens"]


# ------------------------------------------------------------- il timeout

def test_il_timeout_del_client_segue_il_cap(bb, monkeypatch):
    """Review 27/08: alzare il cap senza il timeout esponeva a timeout + retry
    che perde il report intero. Il client va costruito con un timeout >= alla
    stima dell'SDK per il non-streaming (3600 x cap / 128000) e mai sotto i
    240 s di #196 (anti-blocco), con lo stesso `max_retries=1`."""
    costruzioni = []

    class _AnthropicFinto:
        def __init__(self, **kw):
            costruzioni.append(kw)
            self.messages = None
    monkeypatch.setattr(base, "OpenRouterClient", _AnthropicFinto)

    _MockSpecialist(bb)  # senza client: lo costruisce da se'

    assert len(costruzioni) == 1
    kw = costruzioni[0]
    assert kw["timeout"] >= 3600.0 * base.MAX_TOKENS_SPECIALIST / 128000, kw
    assert kw["timeout"] >= 240.0, kw
    assert kw["max_retries"] == 1, kw
    assert kw["timeout"] == base.TIMEOUT_SPECIALIST_S


def test_il_timeout_e_una_funzione_del_cap():
    """Se un giorno il cap sale ancora, il timeout non puo' restare indietro
    in silenzio: il legame e' una FUNZIONE provata su piu' cap, non due numeri
    da tenere allineati (a 16k «450 fisso» sarebbe indistinguibile: e' il
    pavimento e la formula sugli altri cap a rendere il legame misurabile)."""
    assert base.timeout_specialisti(4000) == 240.0      # pavimento di #196
    assert base.timeout_specialisti(12000) == 337.5     # la stima dell'SDK: 3600 x cap / 128000
    assert base.timeout_specialisti(16000) == 450.0
    assert base.timeout_specialisti(32000) == 900.0
    assert base.timeout_specialisti(65536) == 1843.2
    assert base.timeout_specialisti(128000) == 3600.0
    assert base.TIMEOUT_SPECIALIST_S == base.timeout_specialisti(base.MAX_TOKENS_SPECIALIST)


# ---------------------------------------------------------------- il WARN

def test_la_troncatura_dichiara_cap_output_tokens_testo_e_secondi(bb, capsys):
    """Alla prossima troncatura il log deve dire da solo quanto era pensiero
    (cap, output_tokens = pensiero + testo, char del testo visibile) e quanto
    e' durata la call (la velocita' in tok/s e' cio' che decide il timeout)."""
    testo = "x" * 9578
    client = _FakeClient(
        lambda n, kw: _Resp("max_tokens", [_TextBlock(testo)], _Usage(out=16000)))

    _MockSpecialist(bb, client=client).run(2)

    riga = _riga_warn(capsys.readouterr().out)
    assert "cap %d token" % client.calls[0]["max_tokens"] in riga, riga
    assert "output_tokens=16000" in riga, riga
    assert "9578 char" in riga, riga
    assert "ragionamento" in riga, riga
    assert re.search(r"call di \d+\.\d s", riga), riga


def test_usage_assente_la_troncatura_dichiara_nd_non_zero(bb, capsys):
    """Senza usage il numero e' IGNOTO: n.d., mai 0 (regola 14/07) — e la
    dichiarazione non deve esplodere."""
    client = _FakeClient(
        lambda n, kw: _Resp("max_tokens", [_TextBlock("meta' analisi")], usage=None))

    out = _MockSpecialist(bb, client=client).run(2)

    riga = _riga_warn(capsys.readouterr().out)
    assert "output_tokens=n.d." in riga, riga
    assert "output_tokens=0" not in riga, riga
    assert "meta' analisi" in out


def test_senza_troncatura_nessun_warn(bb, capsys):
    client = _FakeClient(lambda n, kw: _Resp("end_turn", [_TextBlock("REPORT")], _Usage()))

    _MockSpecialist(bb, client=client).run(1)

    assert "TRONCATA" not in capsys.readouterr().out


def test_un_altro_stop_nel_ramo_finale_non_e_una_troncatura(bb, capsys):
    """`stop_sequence` entra nello stesso ramo finale di `max_tokens`: il WARN
    deve restare legato al SOLO max_tokens (verde gia' oggi: e' l'ancora della
    mutazione «la riga esce anche senza troncatura» del banco)."""
    client = _FakeClient(
        lambda n, kw: _Resp("stop_sequence", [_TextBlock("REPORT")], _Usage()))

    _MockSpecialist(bb, client=client).run(1)

    assert "TRONCATA" not in capsys.readouterr().out
