# -*- coding: utf-8 -*-
"""Ogni tetto di output (*_MAX_TOKENS) <= tetto del PROVIDER del modello che lo riceve
(voce 7 handoff-4, 05/10/2026, Opus 5.5).

Run del 05/10 21:00: WEEKLY_RED_MAX_TOKENS = 128000 mandato a google/gemini-3.8-flash, che ha
`top_provider.max_completion_tokens` = 65536 nella Models API: il controllo prezzi ha rifiutato
il Red Team dopo R0 e R1 gia' pagati. Qui il confronto si fa PRIMA, per ogni coppia
(tetto, modello configurato per quel ruolo), coi tetti letti dalla fixture della Models API
(tests/fixtures/openrouter_models_snapshot.json, GET gratuita) e i modelli dal .env.example
(e dal .env del PM, letto SOLO per gli slug *_MODEL).

Il valore confrontato e' quello che parte sul FILO: per il Red Team settimanale il codice
abbassa il tetto al limite del provider (red_team._cap_del_provider, 93a71a9) e si misura
QUEL numero, letto con la stessa fixture; la costante nel codice resta 128000 e lo si dice.
Un tetto nuovo nel codice senza riga qui fa fallire il censimento: va mappato al suo ruolo."""
import ast
from pathlib import Path

import pytest

import fornitore_openrouter_finto as ff

RADICE = Path(__file__).resolve().parents[1]
SRC = RADICE / "src" / "bellomberg"
CATALOGO, _DOC = ff.carica_catalogo()
ESEMPIO = ff.variabili_env(RADICE / ".env.example", suffisso="")
PM_SLUG = ff.variabili_env(RADICE / ".env")          # SOLO *_MODEL

_DESK = ("MACRO", "QUANT", "OPTIONS", "FUNDAMENTALS", "CRYPTO", "EVENTDESK")
_CHAT = ("CAPO", "MACRO", "QUANT", "OPTIONS", "FUNDAMENTALS", "CRYPTO", "EVENTDESK", "POLITICS", "NEWS")


def _ruoli_consigliere():
    return ["CONSIGLIERE_MODEL", "CONSIGLIERE_R0_MODEL"] + ["CONSIGLIERE_%s_MODEL" % d for d in _DESK]


def _trade_idea(ruolo):
    from bellomberg.agents.trade_idea import MODEL_IDS
    return "trade_idea:" + ruolo, MODEL_IDS[ruolo]


def _valore_red_settimanale():
    """Il cap che il Red Team settimanale manda davvero (abbassato al tetto del provider)."""
    from bellomberg.agents import red_team

    def sul_filo(slug):
        meta, _ = ff.metadati(CATALOGO, slug)
        taglia = getattr(red_team, "_cap_del_provider", None)
        if taglia is None:   # albero prima di 93a71a9: nessun abbassamento
            return red_team.WEEKLY_RED_MAX_TOKENS
        return taglia(red_team.WEEKLY_RED_MAX_TOKENS, slug, metadata_fn=lambda m: dict(meta))
    return sul_filo


# (nome del tetto, file:costante o variabile .env, valore o funzione slug->valore, ruoli)
# ruolo = nome di variabile *_MODEL, oppure ("trade_idea:<ruolo>", slug fisso nel codice).
def _tetti():
    from bellomberg.agents import capo, red_team, reflection, action_table_extract
    from bellomberg.agents.specialists import base
    from bellomberg.market_data import article_summary, filing_proposta_ai
    from bellomberg.core import llm_client
    import inspect
    sonda = inspect.signature(llm_client.sonda_modelli).parameters["max_tokens"].default
    tutte = sorted(v for v in ESEMPIO if v.endswith("_MODEL"))
    return [
        ("CHAT_MAX_TOKENS (.env.example)", int(ESEMPIO["CHAT_MAX_TOKENS"]),
         ["CHAT_MODEL"] + ["CHAT_%s_MODEL" % a for a in _CHAT]),
        ("capo.CAPO_MAX_TOKENS", capo.CAPO_MAX_TOKENS, ["CAPO_MODEL", _trade_idea("capo")]),
        ("red_team.WEEKLY_RED_MAX_TOKENS (sul filo)", _valore_red_settimanale(), ["RED_TEAM_MODEL"]),
        ("red_team.TRADE_IDEA_RED_MAX_TOKENS", red_team.TRADE_IDEA_RED_MAX_TOKENS, [_trade_idea("red_team")]),
        ("reflection.REFLECTION_MAX_TOKENS", reflection.REFLECTION_MAX_TOKENS, ["REFLECTION_MODEL"]),
        ("action_table_extract.ACTION_EXTRACT_MAX_TOKENS", action_table_extract.ACTION_EXTRACT_MAX_TOKENS,
         ["ACTION_EXTRACTOR_MODEL"]),
        ("base.MAX_TOKENS_SPECIALIST", base.MAX_TOKENS_SPECIALIST, _ruoli_consigliere()),
        ("base.MAX_TOKENS_FUNDAMENTALS_ANALYSIS", base.MAX_TOKENS_FUNDAMENTALS_ANALYSIS,
         ["CONSIGLIERE_FUNDAMENTALS_MODEL", "CONSIGLIERE_MODEL"]),
        ("base.MAX_TOKENS_TRADE_IDEA_AUTHOR", base.MAX_TOKENS_TRADE_IDEA_AUTHOR, [_trade_idea("specialist")]),
        ("article_summary.MAX_TOKENS", article_summary.MAX_TOKENS, ["NEWS_SUMMARY_MODEL"]),
        ("filing_proposta_ai.MAX_TOKENS", filing_proposta_ai.MAX_TOKENS, ["NEWS_SUMMARY_MODEL"]),
        ("llm_client.sonda_modelli(max_tokens=%s)" % sonda, sonda, tutte),
    ]


# Costanti *MAX_TOKENS* intere assegnate a livello di modulo in src/: ognuna deve avere un ruolo sopra.
_MAPPATE = {"CAPO_MAX_TOKENS", "WEEKLY_RED_MAX_TOKENS", "TRADE_IDEA_RED_MAX_TOKENS", "REFLECTION_MAX_TOKENS",
            "ACTION_EXTRACT_MAX_TOKENS", "MAX_TOKENS_SPECIALIST", "MAX_TOKENS_FUNDAMENTALS_ANALYSIS",
            "MAX_TOKENS_TRADE_IDEA_AUTHOR", "article_summary.MAX_TOKENS", "filing_proposta_ai.MAX_TOKENS"}


def test_censimento_dei_tetti_nel_codice():
    trovate = set()
    for path in SRC.rglob("*.py"):
        albero = ast.parse(path.read_text(encoding="utf-8"))
        for nodo in albero.body:
            if not isinstance(nodo, ast.Assign) or not isinstance(nodo.value, ast.Constant):
                continue
            if type(nodo.value.value) is not int:
                continue
            for t in nodo.targets:
                if isinstance(t, ast.Name) and "MAX_TOKENS" in t.id:
                    trovate.add(t.id if t.id != "MAX_TOKENS" else path.stem + ".MAX_TOKENS")
    assert trovate, "censimento vuoto: misura col pattern sbagliato"
    nuove = sorted(trovate - _MAPPATE)
    assert not nuove, ("tetti di output nel codice senza ruolo nel controllo del provider "
                       "(aggiungerli a _tetti() col modello che li riceve): " + ", ".join(nuove))


def _casi():
    configurazioni = [("env_example", ESEMPIO)]
    if PM_SLUG:
        configurazioni.append(("env_pm_slug", {**ESEMPIO, **PM_SLUG}))
    casi = []
    for nome_conf, conf in configurazioni:
        for nome, valore, ruoli in _tetti():
            for ruolo in ruoli:
                if isinstance(ruolo, tuple):
                    etichetta, slug = ruolo
                else:
                    etichetta, slug = ruolo, conf.get(ruolo, "")
                casi.append(pytest.param(nome, valore, etichetta, slug,
                                         id="%s|%s|%s" % (nome_conf, nome.split(" ")[0], etichetta)))
    return casi


@pytest.mark.parametrize("nome,valore,ruolo,slug", _casi())
def test_tetto_entro_il_limite_del_provider(nome, valore, ruolo, slug):
    if not slug:
        pytest.skip(ruolo + " vuota: funzione non configurata, nessun modello che riceva " + nome)
    meta, base = ff.metadati(CATALOGO, slug)
    assert meta is not None, "%s = %s: modello senza riga nella fixture della Models API" % (ruolo, slug)
    tetto = meta["top_provider"]["max_completion_tokens"]
    assert type(tetto) is int and tetto > 0, (slug, tetto)
    richiesti = valore(slug) if callable(valore) else valore
    assert richiesti <= tetto, ("%s = %d supera il tetto del provider %d di %s (%s; Models API, "
                                "top_provider.max_completion_tokens)" % (nome, richiesti, tetto, slug, ruolo))
