# -*- coding: utf-8 -*-
"""Gemelli USA / look-through per i simboli europei (voce 0-quater, 04/10, Opus 5.5).

Cosa si misura:
- il negozio `gemelli_usa` (storage.negozi_privati.carica_gemelli) con le regole delle altre
  famiglie: assente/illeggibile dichiarati, UNA voce malformata = negozio illeggibile;
- le regole proprie: evidenza e fonte obbligatorie, gemello che sia davvero USA, l'OMONIMO senza
  suffisso (classe BA.L -> Boeing) accettato solo se l'evidenza lo nomina, stati coerenti con le
  date, usi in un vocabolario chiuso;
- market_data.lookthrough_usa: SOLO le voci confermate, il perche' dichiarato quando non c'e',
  l'etichetta PROXY come PRIMA chiave del payload;
- tools/ops/proponi_gemelli_usa.py: dry-run che non scrive (misurato), --apply con backup e
  rilettura, rifiuti con l'uscita giusta.
Simboli e numeri INVENTATI (ZZ*/QQ*): nessun valore del book.
"""
import datetime as _dt
import json
import os
import sqlite3
import subprocess

import pytest

import bellomberg.storage.negozi_privati as np_
from bellomberg.market_data import lookthrough_usa as lt
from tools.ops import proponi_gemelli_usa as pg

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESEMPIO = os.path.join(REPO, "src", "bellomberg", "resources", "examples", "gemelli_usa.example.json")
OGGI = _dt.date.today().isoformat()
TRAMITE = "istruzione PM in chat del 02/10, registrata da Claude"


def _gemello(simbolo="QQSYN", evidenza="factsheet: stesso indice Esempio 100", fonte="KID 2026-09"):
    return {"simbolo": simbolo, "evidenza": evidenza, "fonte": fonte}


def _voce(**sovra):
    v = {"stato": "confermato", "relazione": "stesso_indice", "gemello_usa": _gemello(),
         "usi_ammessi": ["opzioni"], "proposto_il": "2026-10-01", "confermato_il": "2026-10-02",
         "confermato_tramite": TRAMITE}
    v.update(sovra)
    return {k: x for k, x in v.items() if x is not None}


def _negozio(tmp_path, voci, nome="gemelli.json"):
    p = tmp_path / nome
    p.write_text(json.dumps(voci), encoding="utf-8")
    return str(p)


def _carica(tmp_path, voci):
    return np_.carica_gemelli(_negozio(tmp_path, voci))


# ------------------------------------------------ A. il negozio

def test_esempio_si_carica_col_caricatore_vero():
    r = np_.carica_gemelli(ESEMPIO)
    assert r["motivo"] is None, r["motivo"]
    stati = {t: v["stato"] for t, v in r["gemelli"].items()}
    assert set(stati.values()) == {"proposto", "confermato", "rifiutato"}


def test_negozio_assente_dichiara_origine_e_motivo(tmp_path):
    r = np_.carica_gemelli(str(tmp_path / "manca.json"))
    assert r["gemelli"] == {} and r["origine"] == "assente"
    assert "gemelli_usa.example.json" in r["motivo"]


def test_voce_valida_si_carica(tmp_path):
    r = _carica(tmp_path, {"ZZIDX.MI": _voce()})
    assert r["motivo"] is None
    assert r["gemelli"]["ZZIDX.MI"]["gemello_usa"]["simbolo"] == "QQSYN"


@pytest.mark.parametrize("voce,parola", [
    (_voce(stato="approvato"), "stato"),
    (_voce(gemello_usa=_gemello(simbolo="QQSYN.L")), "USA"),
    (_voce(gemello_usa=_gemello(simbolo="BTC")), "USA"),
    (_voce(gemello_usa=_gemello(evidenza="  ")), "evidenza"),
    (_voce(gemello_usa=_gemello(fonte="")), "fonte"),
    (_voce(confermato_il=None), "confermato_il"),
    (_voce(stato="proposto", confermato_tramite=None), "confermato_il"),
    (_voce(stato="proposto", confermato_il=None), "confermato_tramite"),
    (_voce(confermato_tramite=None), "confermato_tramite"),
    (_voce(confermato_tramite="  "), "confermato_tramite"),
    (_voce(usi_ammessi=["vol"]), "vol"),
    (_voce(usi_ammessi=[]), "usi_ammessi"),
    (_voce(relazione="cugino"), "relazione"),
    (_voce(confermato_il="2026-09-01"), "proposto_il"),
    (_voce(campo_ignoto=1), "campo_ignoto"),
])
def test_una_voce_malformata_rende_illeggibile_il_negozio_intero(tmp_path, voce, parola):
    r = _carica(tmp_path, {"ZZIDX.MI": voce, "ZZBUONO.MI": _voce()})
    assert r["origine"] == "illeggibile" and r["gemelli"] == {}
    assert parola in r["motivo"], r["motivo"]


def test_chiave_usa_e_malformata(tmp_path):
    r = _carica(tmp_path, {"QQUSA": _voce()})
    assert r["origine"] == "illeggibile" and "USA" in r["motivo"]


def test_omonimo_senza_suffisso_senza_evidenza_che_lo_nomina_e_rifiutato(tmp_path):
    """La trappola BA.L -> Boeing: il simbolo USA uguale alla base e' di solito un ALTRO
    strumento. Un'evidenza generica non basta: deve nominarlo."""
    v = _voce(relazione="adr", gemello_usa=_gemello(simbolo="ZZBETA", evidenza="stessa societa', vedi sito"))
    r = _carica(tmp_path, {"ZZBETA.DE": v})
    assert r["origine"] == "illeggibile" and "OMONIMO" in r["motivo"], r["motivo"]


def test_omonimo_con_evidenza_che_lo_nomina_e_accettato(tmp_path):
    v = _voce(relazione="adr", gemello_usa=_gemello(
        simbolo="ZZBETA", evidenza="ZZBETA su NYSE e' l'ADR 1:1 dello stesso emittente (20-F)"))
    r = _carica(tmp_path, {"ZZBETA.DE": v})
    assert r["motivo"] is None


def test_omonimo_nominato_solo_come_parte_di_un_altro_simbolo_non_basta(tmp_path):
    v = _voce(relazione="adr", gemello_usa=_gemello(simbolo="ZZB", evidenza="vedi ZZBX su NYSE"))
    r = _carica(tmp_path, {"ZZB.DE": v})
    assert r["origine"] == "illeggibile" and "OMONIMO" in r["motivo"]


@pytest.mark.parametrize("evidenza", ["factsheet di ZZB.L, stesso emittente", "ZZB.L = ZZB.DE?", "vedi XZZB"])
def test_omonimo_citato_solo_col_suffisso_non_conta(tmp_path, evidenza):
    """Review RV-C P2-2: «ZZB.L» contiene «ZZB» ma NON nomina il simbolo USA."""
    v = _voce(relazione="adr", gemello_usa=_gemello(simbolo="ZZB", evidenza=evidenza))
    r = _carica(tmp_path, {"ZZB.L": v})
    assert r["origine"] == "illeggibile" and "OMONIMO" in r["motivo"], r["motivo"]


@pytest.mark.parametrize("simbolo", ["ZZQ.MC", "ZZQ-EUR", "XRPZ-USD", "^ZZX", "ZZ=X", "ZZQ.F"])
def test_gemello_che_la_guardia_non_dice_usa_e_rifiutato(tmp_path, simbolo):
    """Review RV-C P2-5: `mercato_di` chiamava USA tutto cio' che non e' nel registro."""
    r = _carica(tmp_path, {"ZZIDX.MI": _voce(gemello_usa=_gemello(simbolo=simbolo))})
    assert r["origine"] == "illeggibile" and "USA" in r["motivo"], r["motivo"]


def test_classe_di_azioni_col_punto_non_e_ammessa_finche_la_guardia_non_la_dice_usa(tmp_path):
    """Limite dichiarato (decisione main 04/10: nessuna logica doppia, conta solo `coperto`)."""
    r = _carica(tmp_path, {"ZZIDX.MI": _voce(gemello_usa=_gemello(simbolo="QQB.B"))})
    assert r["origine"] == "illeggibile" and "USA" in r["motivo"]


@pytest.mark.parametrize("chiave,parola", [("ZZC-USD", "crypto"), ("BTC", "crypto"),
                                           ("QQUSA", "USA")])
def test_chiave_crypto_o_usa_e_malformata(tmp_path, chiave, parola):
    r = _carica(tmp_path, {chiave: _voce()})
    assert r["origine"] == "illeggibile" and parola in r["motivo"], r["motivo"]


def test_chiave_su_listino_non_censito_e_ammessa(tmp_path):
    assert _carica(tmp_path, {"ZZQ.MC": _voce()})["motivo"] is None


def test_partecipazioni_forma_e_pesi(tmp_path):
    part = {"fonte": "factsheet", "data_riferimento": "2026-08-31",
            "voci": [{"simbolo_usa": "QQAAA", "peso_pct": 60}, {"simbolo_usa": "QQBBB", "peso_pct": 50}]}
    v = _voce(relazione="partecipazioni", gemello_usa=None, partecipazioni=part)
    r = _carica(tmp_path, {"ZZGAMMA.L": v})
    assert r["origine"] == "illeggibile" and "100" in r["motivo"]
    part["voci"][1]["peso_pct"] = 5
    assert _carica(tmp_path, {"ZZGAMMA.L": v})["motivo"] is None
    v["gemello_usa"] = _gemello()
    assert _carica(tmp_path, {"ZZGAMMA.L": v})["origine"] == "illeggibile"


def test_rifiutato_senza_proposta_e_valido_ma_vuole_il_motivo(tmp_path):
    v = {"stato": "rifiutato", "proposto_il": "2026-10-01", "rifiutato_il": "2026-10-02",
         "motivo_rifiuto": "nessun gemello"}
    assert _carica(tmp_path, {"ZZDELTA.MI": v})["motivo"] is None
    del v["motivo_rifiuto"]
    assert _carica(tmp_path, {"ZZDELTA.MI": v})["origine"] == "illeggibile"


# ------------------------------------------------ B. lookthrough_usa: solo confermate

@pytest.fixture
def negozio_misto(tmp_path, monkeypatch):
    p = _negozio(tmp_path, {
        "ZZIDX.MI": _voce(usi_ammessi=["opzioni", "notizie"]),
        "ZZPROP.MI": _voce(stato="proposto", confermato_il=None, confermato_tramite=None),
        "ZZRIF.MI": _voce(stato="rifiutato", rifiutato_il="2026-10-03", motivo_rifiuto="no"),
        "ZZGAMMA.L": _voce(relazione="partecipazioni", gemello_usa=None, usi_ammessi=["congress"],
                           partecipazioni={"fonte": "factsheet", "data_riferimento": "2026-08-31",
                                           "voci": [{"simbolo_usa": "QQAAA", "peso_pct": 12.5}]}),
    })
    monkeypatch.setattr(np_, "PERCORSO_GEMELLI", p)
    return p


def test_solo_le_voci_confermate_escono(negozio_misto):
    v = lt.gemello_confermato("zzidx.mi")
    assert v["ticker_book"] == "ZZIDX.MI" and v["gemello_usa"]["simbolo"] == "QQSYN"
    assert lt.gemello_confermato("ZZPROP.MI") is None
    assert lt.gemello_confermato("ZZRIF.MI") is None
    assert lt.esito_gemello("ZZPROP.MI")["stato"] == "proposto"
    assert lt.esito_gemello("ZZRIF.MI")["stato"] == "rifiutato"
    assert "conferma" in lt.esito_gemello("ZZPROP.MI")["motivo"]


def test_uso_non_ammesso_e_dichiarato(negozio_misto):
    assert lt.gemello_confermato("ZZIDX.MI", "insider") is None
    e = lt.esito_gemello("ZZIDX.MI", "insider")
    assert e["stato"] == "uso_non_ammesso" and "insider" in e["motivo"]
    assert lt.gemello_confermato("ZZIDX.MI", "opzioni") is not None
    with pytest.raises(ValueError):
        lt.esito_gemello("ZZIDX.MI", "inventato")


def test_nessuna_voce_e_negozio_assente_o_illeggibile_sono_stati_dichiarati(tmp_path, monkeypatch, negozio_misto):
    e = lt.esito_gemello("ZZALTRO.MI")
    assert e["stato"] == "nessuna_voce" and e["voce"] is None and "proponi_gemelli_usa" in e["motivo"]
    monkeypatch.setattr(np_, "PERCORSO_GEMELLI", str(tmp_path / "manca.json"))
    e = lt.esito_gemello("ZZIDX.MI")
    assert e["stato"] == "negozio_assente" and e["motivo"]
    rotto = tmp_path / "rotto.json"
    rotto.write_text("{ non json", encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_GEMELLI", str(rotto))
    e = lt.esito_gemello("ZZIDX.MI")
    assert e["stato"] == "negozio_illeggibile" and e["motivo"]


def test_etichetta_proxy_dice_cosa_chi_e_quando(negozio_misto):
    v = lt.gemello_confermato("ZZIDX.MI", "opzioni")
    et = lt.etichetta_proxy(v, "opzioni")
    assert list(et)[0] == "etichetta"
    assert et["etichetta"].startswith("PROXY")
    for pezzo in ("QQSYN", "ZZIDX.MI", "2026-10-02"):
        assert pezzo in et["etichetta"], et["etichetta"]
    assert et["ticker_book"] == "ZZIDX.MI" and et["ticker_dati"] == "QQSYN"
    assert et["confermato_il"] == "2026-10-02" and et["fonte_evidenza"] == "KID 2026-09"
    # la conferma dice COME e' stata registrata, nella frase che il desk legge
    assert et["confermato_tramite"] == TRAMITE
    assert "confermato dal PM il 2026-10-02 (%s)" % TRAMITE in et["etichetta"], et["etichetta"]


def test_etichetta_rifiutata_su_voce_non_confermata_o_uso_non_ammesso(negozio_misto):
    v = lt.gemello_confermato("ZZIDX.MI")
    with pytest.raises(ValueError):
        lt.etichetta_proxy(dict(v, stato="proposto"), "opzioni")
    with pytest.raises(ValueError):
        lt.etichetta_proxy(v, "insider")
    with pytest.raises(ValueError):
        lt.etichetta_proxy({k: x for k, x in v.items() if k != "confermato_tramite"}, "opzioni")


def test_partecipazioni_vecchie_sono_stale_nell_etichetta(negozio_misto):
    v = lt.gemello_confermato("ZZGAMMA.L", "congress")
    fresca = lt.etichetta_proxy(v, "congress", oggi=_dt.date(2026, 9, 10))
    assert fresca["partecipazioni_stale"] is False and "STALE" not in fresca["etichetta"]
    assert fresca["ticker_dati"] == ["QQAAA"] and "2026-08-31" in fresca["etichetta"]
    vecchia = lt.etichetta_proxy(v, "congress", oggi=_dt.date(2027, 3, 1))
    assert vecchia["partecipazioni_stale"] is True and "STALE" in vecchia["etichetta"]
    assert vecchia["eta_partecipazioni_giorni"] == (_dt.date(2027, 3, 1) - _dt.date(2026, 8, 31)).days


def test_etichetta_e_la_prima_chiave_del_payload(negozio_misto):
    et = lt.etichetta_proxy(lt.gemello_confermato("ZZIDX.MI"), "opzioni")
    payload = {"iv": 0.25, "scadenze": ["2026-12-18"]}
    out = lt.metti_in_testa(payload, et)
    assert list(out)[0] == "proxy" and out["proxy"] is et
    assert {k: out[k] for k in payload} == payload
    # e un tetto che taglia in CODA non la tocca
    assert json.dumps(out)[:200].find("PROXY") != -1
    with pytest.raises(ValueError):
        lt.metti_in_testa(out, et)
    assert "PROXY" in lt.source_con_proxy("polygon opzioni", et)


# ------------------------------------------------ C. lo script

@pytest.fixture
def db(tmp_path):
    p = tmp_path / "book.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE positions (ticker TEXT, is_active INTEGER)")
    con.executemany("INSERT INTO positions VALUES (?, ?)",
                    [("ZZIDX.MI", 1), ("ZZBETA.DE", 1), ("QQUSA", 1), ("ZZCHIUSA.MI", 0)])
    con.commit()
    con.close()
    return str(p)


def _proponi(dest, db, *extra):
    return pg.main(["--proponi", "ZZIDX.MI", "--gemello", "QQSYN", "--relazione", "stesso_indice",
                    "--evidenza", "factsheet: stesso indice Esempio 100", "--fonte", "KID 2026-09",
                    "--usi", "opzioni,notizie", "--dest", dest, "--db", db, *extra])


def test_senza_argomenti_elenca_i_non_usa_senza_voce(tmp_path, db, capsys):
    dest = str(tmp_path / "g.json")
    assert pg.main(["--dest", dest, "--db", db]) == 0
    out = capsys.readouterr().out
    assert "ZZIDX.MI" in out and "ZZBETA.DE" in out
    assert "QQUSA" not in out and "ZZCHIUSA.MI" not in out
    assert "MODELLO" in out and not os.path.exists(dest)


def test_senza_db_e_un_ko_dichiarato(tmp_path, capsys):
    assert pg.main(["--dest", str(tmp_path / "g.json"), "--db", str(tmp_path / "manca.db")]) == 2
    assert "KO" in capsys.readouterr().out


def test_dry_run_non_scrive(tmp_path, db):
    dest = str(tmp_path / "g.json")
    assert _proponi(dest, db) == 0
    assert not os.path.exists(dest)
    assert _proponi(dest, db, "--apply") == 0
    prima = open(dest, "rb").read()
    assert pg.main(["--conferma", "ZZIDX.MI", "--tramite", TRAMITE, "--dest", dest, "--db", db]) == 0
    assert open(dest, "rb").read() == prima


def test_proponi_conferma_apply_con_backup_e_rilettura(tmp_path, db, monkeypatch):
    dest = str(tmp_path / "g.json")
    assert _proponi(dest, db, "--apply") == 0
    assert np_.carica_gemelli(dest)["gemelli"]["ZZIDX.MI"]["stato"] == "proposto"
    monkeypatch.setattr(np_, "PERCORSO_GEMELLI", dest)
    assert lt.gemello_confermato("ZZIDX.MI") is None          # proposta: non si usa
    assert pg.main(["--conferma", "ZZIDX.MI", "--dest", dest, "--db", db, "--apply"]) == 1   # senza --tramite
    assert np_.carica_gemelli(dest)["gemelli"]["ZZIDX.MI"]["stato"] == "proposto"
    assert pg.main(["--conferma", "ZZIDX.MI", "--tramite", TRAMITE, "--dest", dest, "--db", db, "--apply"]) == 0
    v = np_.carica_gemelli(dest)["gemelli"]["ZZIDX.MI"]
    assert v["stato"] == "confermato" and v["confermato_il"] == OGGI and v["confermato_tramite"] == TRAMITE
    assert [f for f in os.listdir(str(tmp_path)) if f.startswith("g.json.bak-")]
    assert lt.gemello_confermato("ZZIDX.MI", "opzioni")["gemello_usa"]["simbolo"] == "QQSYN"


def test_omonimo_senza_evidenza_che_lo_nomina_rifiutato_dallo_script(tmp_path, db, capsys):
    dest = str(tmp_path / "g.json")
    rc = pg.main(["--proponi", "ZZBETA.DE", "--gemello", "ZZBETA", "--relazione", "adr",
                  "--evidenza", "stessa societa'", "--fonte", "sito", "--usi", "opzioni",
                  "--dest", dest, "--db", db, "--apply"])
    assert rc == 1 and not os.path.exists(dest)
    assert "OMONIMO" in capsys.readouterr().out


def test_evidenza_mancante_rifiutata(tmp_path, db):
    dest = str(tmp_path / "g.json")
    rc = pg.main(["--proponi", "ZZIDX.MI", "--gemello", "QQSYN", "--relazione", "stesso_indice",
                  "--fonte", "KID", "--usi", "opzioni", "--dest", dest, "--db", db, "--apply"])
    assert rc == 1 and not os.path.exists(dest)


def test_simbolo_fuori_book_rifiutato_salvo_esplicito(tmp_path, db):
    dest = str(tmp_path / "g.json")
    args = ["--proponi", "ZZFUORI.MI", "--gemello", "QQSYN", "--relazione", "stesso_indice",
            "--evidenza", "factsheet", "--fonte", "KID", "--usi", "opzioni", "--dest", dest, "--db", db]
    assert pg.main(args) == 1
    assert pg.main(args + ["--anche-fuori-book"]) == 0


def test_stati_sbagliati_rifiutati(tmp_path, db):
    dest = str(tmp_path / "g.json")
    assert pg.main(["--conferma", "ZZIDX.MI", "--tramite", TRAMITE, "--dest", dest, "--db", db]) == 1  # senza voce
    assert pg.main(["--rifiuta", "ZZIDX.MI", "--dest", dest, "--db", db]) == 1       # senza motivo
    assert _proponi(dest, db, "--apply") == 0
    assert _proponi(dest, db, "--apply") == 1                                        # gia' c'e'
    assert pg.main(["--rifiuta", "ZZIDX.MI", "--motivo", "indice diverso", "--dest", dest,
                    "--db", db, "--apply"]) == 0
    assert pg.main(["--conferma", "ZZIDX.MI", "--tramite", TRAMITE, "--dest", dest, "--db", db]) == 1  # rifiutato
    v = np_.carica_gemelli(dest)["gemelli"]["ZZIDX.MI"]
    assert v["stato"] == "rifiutato" and v["motivo_rifiuto"] == "indice diverso"


def test_rifiuta_senza_voce_dichiara_nessun_gemello(tmp_path, db):
    dest = str(tmp_path / "g.json")
    assert pg.main(["--rifiuta", "ZZBETA.DE", "--motivo", "nessun gemello", "--dest", dest,
                    "--db", db, "--apply"]) == 0
    assert np_.carica_gemelli(dest)["gemelli"]["ZZBETA.DE"]["stato"] == "rifiutato"


def test_negozio_illeggibile_ferma_e_non_tocca_il_file(tmp_path, db):
    dest = tmp_path / "g.json"
    dest.write_text('{"ZZIDX.MI": {"stato": "boh"}}', encoding="utf-8")
    prima = dest.read_bytes()
    assert _proponi(str(dest), db, "--apply", "--sostituisci") == 2
    assert dest.read_bytes() == prima


def test_backup_distinti_su_apply_ravvicinati(tmp_path, db):
    """Review RV-C P3: col backup al secondo, quattro --apply ravvicinati lasciavano un solo .bak."""
    dest = str(tmp_path / "g.json")
    assert _proponi(dest, db, "--apply") == 0
    for motivo in ("uno", "due", "tre"):
        assert _proponi(dest, db, "--apply", "--sostituisci", "--note", motivo) == 0
    baks = [f for f in os.listdir(str(tmp_path)) if f.startswith("g.json.bak-")]
    assert len(baks) == 3, baks
    assert not [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]


def test_rilettura_fallita_e_un_ko_dello_strumento(tmp_path, db, monkeypatch, capsys):
    dest = str(tmp_path / "g.json")

    def scrittura_rotta(d, testo):
        open(d, "w", encoding="utf-8").write("{ non json")
        return None
    monkeypatch.setattr(pg, "scrivi_atomico", scrittura_rotta)
    assert _proponi(dest, db, "--apply") == 2
    assert "KO" in capsys.readouterr().out


def test_il_default_dello_script_e_il_negozio_privato():
    """Le prove passano sempre --dest/--db: il default resta quello vero, e nessuna prova lo scrive."""
    assert os.path.basename(np_.PERCORSO_GEMELLI) == "gemelli_usa.json"


# ------------------------------------------------ D. l'export (rossi finche' l'esempio non e' tracciato)

def test_esempio_SELEZIONATO_dall_export_e_il_negozio_no():
    allowlist = os.path.join(REPO, "tools", "release", "policy", "ALLOWLIST.txt")
    if not os.path.exists(allowlist):
        pytest.skip("policy/ALLOWLIST.txt assente: siamo nel tree pubblico (P2)")
    from tools.release import export_pubblico as ep
    from tools.release import verifica_pubblico as vp
    tracciati = ep.file_tracciati(REPO)
    scelti, _ = ep.seleziona(tracciati, vp.leggi_lista(allowlist))
    assert "src/bellomberg/resources/examples/gemelli_usa.example.json" in scelti
    assert "tools/ops/proponi_gemelli_usa.py" in scelti
    assert "data/gemelli_usa.json" not in scelti and "data/gemelli_usa.json" not in tracciati


def test_esempio_tracciato_e_negozio_ignorato():
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, encoding="utf-8", errors="replace").returncode
    rel = "src/bellomberg/resources/examples/gemelli_usa.example.json"
    assert git("ls-files", "--error-unmatch", rel) == 0, "%s non e' tracciato" % rel
    assert git("check-ignore", rel) == 1
    assert git("check-ignore", "data/gemelli_usa.json") == 0
