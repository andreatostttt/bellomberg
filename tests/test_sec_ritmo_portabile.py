"""Ritmo SEC/ESEF condiviso fra processi anche su Windows (revisione 04/10, R1/R5).

Prima il lock usava solo fcntl: su Windows ripiegava ZITTO sul ritmo del solo processo
(nessun file creato, backend + comitato fino a ~16 req/s contro il limite SEC di 10).
Nessuna rete: solo il file del ritmo nel tmp del test.
"""
import ast
import threading
import time
from pathlib import Path

from bellomberg.market_data import esef, sec_edgar, sec_xbrl


def _due_attori(percorso, intervallo, monkeypatch, n=6):
    """Due attori con lock di thread e memoria locale DISTINTI (come due processi):
    li coordina solo il file del ritmo. Si registra l'istante SCRITTO, letto dentro il lock."""
    import os
    istanti = []
    sblocca = sec_edgar._sblocca

    def leggi_e_sblocca(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        istanti.append(float(os.read(fd, sec_edgar._RITMO_LARGHEZZA).decode("ascii").strip()))
        sblocca(fd)
    monkeypatch.setattr(sec_edgar, "_sblocca", leggi_e_sblocca)

    def attore():
        lock, ultima = threading.Lock(), [0.0]
        for _ in range(n):
            sec_edgar._attendi(percorso, intervallo, lock, ultima)

    fili = [threading.Thread(target=attore) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(30)
    istanti.sort()
    return [b - a for a, b in zip(istanti, istanti[1:])]


def test_lock_del_file_coordina_due_attori_indipendenti(tmp_path, monkeypatch):
    gap = _due_attori(tmp_path / ".sec_ritmo", 0.05, monkeypatch)
    assert len(gap) == 11
    assert min(gap) >= 0.05 - 0.001, gap


def test_gara_vera_il_secondo_aspetta_chi_tiene_il_lock(tmp_path, monkeypatch):
    """Gara forzata: B entra mentre A dorme dentro la sezione critica. Senza lock tra processi
    B legge lo stesso istante di A e scrivono insieme; col lock B aspetta la scrittura di A."""
    import os
    percorso = tmp_path / ".sec_ritmo"
    intervallo = 2.0
    # A dorme se parte entro 2 s dalla scrittura (margine per una macchina carica: con 0,5 s
    # il test e' caduto una volta senza che A arrivasse a dormire)
    percorso.write_bytes(f"{time.time():.6f}".ljust(sec_edgar._RITMO_LARGHEZZA).encode("ascii"))
    scritti = []
    sblocca = sec_edgar._sblocca

    def leggi_e_sblocca(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        scritti.append(float(os.read(fd, sec_edgar._RITMO_LARGHEZZA).decode("ascii").strip()))
        sblocca(fd)
    monkeypatch.setattr(sec_edgar, "_sblocca", leggi_e_sblocca)

    a_dorme, b_in_attesa = threading.Event(), threading.Event()

    def dormi(s):
        # A (dentro la sezione critica) non riparte finche' B non e' arrivato a dormire: B ci arriva
        # bloccato sul lock (cura) oppure dopo aver letto lo stesso istante di A (difetto).
        if threading.current_thread().name == "A":
            a_dorme.set()
            assert b_in_attesa.wait(20)
        else:
            b_in_attesa.set()
        time.sleep(s)
    import types
    monkeypatch.setattr(sec_edgar, "time", types.SimpleNamespace(time=time.time, monotonic=time.monotonic,
                                                                  sleep=dormi))

    def attore():
        sec_edgar._attendi(percorso, intervallo, threading.Lock(), [0.0])

    a = threading.Thread(target=attore, name="A")
    a.start()
    assert a_dorme.wait(20)
    b = threading.Thread(target=attore, name="B")
    b.start()
    a.join(10)
    b.join(10)
    assert len(scritti) == 2
    assert abs(scritti[1] - scritti[0]) >= intervallo - 0.001, scritti


def test_il_file_del_ritmo_esiste_e_lo_stato_dice_condiviso(tmp_path):
    percorso = tmp_path / ".sec_ritmo"
    sec_edgar.attendi_sec(percorso=percorso)
    assert percorso.is_file()
    assert float(percorso.read_bytes()[:32].decode("ascii").strip()) > 0
    stato = sec_edgar.stato_ritmo()[str(percorso)]
    assert stato["condiviso"] is True and stato["motivo"] is None


def test_lock_inesistente_e_dichiarato_e_ritmo_a_un_quarto(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sec_edgar, "fcntl", None)
    monkeypatch.setattr(sec_edgar, "msvcrt", None)
    percorso = tmp_path / ".sec_ritmo"
    sec_edgar.attendi_sec(percorso=percorso)
    t0 = time.monotonic()
    sec_edgar.attendi_sec(percorso=percorso)
    trascorso = time.monotonic() - t0
    stato = sec_edgar.stato_ritmo()[str(percorso)]
    assert stato["condiviso"] is False and "lock" in stato["motivo"]
    # ripiego per processo: intervallo moltiplicato per i processi attesi, il tetto totale regge
    # ripiego per processo a 1/4 del tetto: 8 req/s regge fino a 4 processi (REV_G2a R-2: 1/2 sforava con 3)
    assert trascorso >= sec_edgar._SEC_INTERVALLO_S * 4 - 0.02
    assert "ritmo NON condiviso" in capsys.readouterr().out


def test_tetto_totale_dichiarato_sotto_il_limite_sec():
    assert 1 / sec_edgar._SEC_INTERVALLO_S <= 8
    assert sec_edgar.stato_ritmo.__doc__ and "8" in sec_edgar.stato_ritmo.__doc__


def test_esef_usa_lo_stesso_lock_portabile(tmp_path):
    percorso = tmp_path / ".esef_ritmo"
    esef.attendi_esef(percorso=percorso)
    assert percorso.is_file()
    assert sec_edgar.stato_ritmo()[str(percorso)]["condiviso"] is True


def _funzioni_con_get_senza_ritmo(modulo, ritmo_ammessi):
    albero = ast.parse(Path(modulo.__file__).read_text(encoding="utf-8"))
    scoperte = []
    for nodo in ast.walk(albero):
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        chiamate = [c for c in ast.walk(nodo) if isinstance(c, ast.Call)]
        rete = [c for c in chiamate if isinstance(c.func, ast.Attribute)
                and c.func.attr in ("get", "head") and isinstance(c.func.value, ast.Name)
                and c.func.value.id == "requests"]
        ritmo = [c for c in chiamate if (isinstance(c.func, ast.Name) and c.func.id in ritmo_ammessi | {"ritmo"})
                 or (isinstance(c.func, ast.Attribute) and c.func.attr in ritmo_ammessi)]
        if rete and not ritmo:
            scoperte.append(nodo.name)
    return scoperte


def test_ogni_richiesta_sec_passa_dal_ritmo_companyfacts_compreso():
    # Ogni funzione che chiama requests.get/head deve chiamare il ritmo nel suo corpo.
    assert _funzioni_con_get_senza_ritmo(sec_edgar, {"attendi_sec"}) == []
    assert _funzioni_con_get_senza_ritmo(sec_xbrl, {"attendi_sec"}) == []


def test_ogni_richiesta_esef_passa_dal_ritmo():
    # GLEIF (candidati_gleif) e' un altro host: fuori da questo ritmo, dichiarato nel rapporto.
    scoperte = set(_funzioni_con_get_senza_ritmo(esef, {"attendi_esef"})) - {"candidati_gleif"}
    assert scoperte == set()


def test_companyfacts_chiama_il_ritmo_prima_della_rete(tmp_path, monkeypatch):
    ordine = []
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sec_xbrl, "_cache_path", lambda cik: str(tmp_path / f"CIK{cik}.json"))
    monkeypatch.setattr(sec_edgar, "attendi_sec", lambda **k: ordine.append("ritmo"))
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")

    class R:
        status_code = 200

        def json(self):
            return {"facts": {}}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: ordine.append("rete") or R())
    sec_xbrl._fetch_companyfacts("0009990001")
    assert ordine == ["ritmo", "rete"]


def test_storico_esef_del_dcf_passa_dal_ritmo(monkeypatch):
    visti = []

    def cerca(nome, ritmo=None):
        visti.append(("cerca", ritmo))
        return [], ["x"], None, "fermo qui"

    def elenco(lei, max_pages=20, ritmo=None):
        visti.append(("elenco", ritmo))
        raise RuntimeError("fermo qui")

    monkeypatch.setattr(esef, "_cerca_entita", cerca)
    monkeypatch.setattr(esef, "_list_filings", elenco)
    monkeypatch.setattr(esef, "_load_cache", lambda lei: {})
    monkeypatch.setattr("bellomberg.storage.negozi_privati.carica_lei",
                        lambda: {"lei": {}, "origine": "vuoto", "motivo": None})
    esef.resolve_lei("ACME.PA", company_name="Acme SA")
    esef._refresh_entity_cache("999900ACME0000000001")
    assert visti == [("cerca", esef.attendi_esef), ("elenco", esef.attendi_esef)]


def _tieni_il_lock(percorso):
    """Un altro «processo» (altro handle) tiene il lock del ritmo."""
    import os
    percorso.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(percorso, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    if sec_edgar.fcntl is not None:
        sec_edgar.fcntl.flock(fd, sec_edgar.fcntl.LOCK_EX)
    else:
        os.lseek(fd, sec_edgar._RITMO_BYTE_LOCK, os.SEEK_SET)
        sec_edgar.msvcrt.locking(fd, sec_edgar.msvcrt.LK_NBLCK, 1)
    return fd


def _rilascia(fd):
    import os
    if sec_edgar.fcntl is not None:
        sec_edgar.fcntl.flock(fd, sec_edgar.fcntl.LOCK_UN)
    else:
        os.lseek(fd, sec_edgar._RITMO_BYTE_LOCK, os.SEEK_SET)
        sec_edgar.msvcrt.locking(fd, sec_edgar.msvcrt.LK_UNLCK, 1)
    os.close(fd)


def test_lock_occupato_a_lungo_si_aspetta_dichiarando_mai_fuori_dal_file(tmp_path, monkeypatch):
    # REV_G2a R-2: allo scadere del lock si partiva col ripiego per processo (fuori dal file).
    monkeypatch.setattr(sec_edgar, "_RITMO_ATTESA_LOCK_S", 0.2, raising=False)
    monkeypatch.setattr(sec_edgar, "_RITMO_ATTESA_MAX_S", 30.0, raising=False)
    percorso = tmp_path / ".sec_ritmo"
    fd = _tieni_il_lock(percorso)
    finito = threading.Event()
    t = threading.Thread(target=lambda: (sec_edgar.attendi_sec(percorso=percorso), finito.set()))
    t.start()
    time.sleep(0.6)
    try:
        assert not finito.is_set(), "la richiesta e' partita senza il lock del ritmo"
        stato = sec_edgar.stato_ritmo()[str(percorso)]
        assert stato["condiviso"] is True and "attendo" in stato["motivo"]
    finally:
        _rilascia(fd)
    t.join(10)
    assert finito.is_set()
    assert sec_edgar.stato_ritmo()[str(percorso)]["motivo"] is None
    assert float(percorso.read_bytes()[:32].decode("ascii").strip()) > 0


def test_lock_mai_rilasciato_la_richiesta_non_parte(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_RITMO_ATTESA_LOCK_S", 0.1, raising=False)
    monkeypatch.setattr(sec_edgar, "_RITMO_ATTESA_MAX_S", 0.4, raising=False)
    percorso = tmp_path / ".sec_ritmo"
    fd = _tieni_il_lock(percorso)
    try:
        import pytest
        with pytest.raises(getattr(sec_edgar, "RitmoBloccato", RuntimeError)) as exc:
            sec_edgar.attendi_sec(percorso=percorso)
        assert "NON eseguita" in str(exc.value)
    finally:
        _rilascia(fd)


# ---------------------------------------------------------------- guardia su tutto src (REV_G2a R-3)

_HOST_RITMATI = ("sec.gov", "xbrl.org", "gleif.org")
_RITMI = {"attendi_sec", "attendi_esef", "attendi_gleif", "ritmo"}
# agents/trade_idea_sources.py: file di un'altra sessione (HTTPSConnection, non requests): lo segnala il coordinatore.


def _nome_chiamata(f):
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def _scoperte_in_src():
    radice = Path(sec_edgar.__file__).resolve().parents[1]
    scoperte = []
    for f in sorted(radice.rglob("*.py")):
        testo = f.read_text(encoding="utf-8")
        albero = ast.parse(testo)
        costanti = {t.id: n.value.value for n in albero.body if isinstance(n, ast.Assign)
                    and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
                    for t in n.targets if isinstance(t, ast.Name)}
        for c in ast.walk(albero):  # nessun chiamante puo' spegnere il ritmo
            if isinstance(c, ast.Call) and any(k.arg == "ritmo" and isinstance(k.value, ast.Constant)
                                               and k.value.value is None for k in c.keywords):
                scoperte.append(f"{f.name}:{c.lineno} ritmo=None")
        for fn in ast.walk(albero):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            seg = ast.get_source_segment(testo, fn) or ""
            usati = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
            host = any(h in seg for h in _HOST_RITMATI) or any(
                h in costanti[u] for u in usati if u in costanti for h in _HOST_RITMATI)
            rete = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr in ("get", "head", "post", "request") and isinstance(c.func.value, ast.Name)
                    and c.func.value.id in ("requests", "_req")]
            ritmi = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and _nome_chiamata(c.func) in _RITMI]
            if host and rete and not ritmi:
                scoperte.append(f"{f.name}:{fn.lineno} {fn.name} senza ritmo")
            for se in (n for n in ast.walk(fn) if isinstance(n, ast.If)):  # ritmo() condizionato a «ritmo»
                if any(isinstance(n, ast.Name) and n.id == "ritmo" for n in ast.walk(se.test)) and any(
                        isinstance(c, ast.Call) and _nome_chiamata(c.func) == "ritmo"
                        for b in se.body for c in ast.walk(b)):
                    scoperte.append(f"{f.name}:{se.lineno} {fn.name} ritmo condizionato")
    return scoperte


def test_ogni_richiesta_sec_esef_gleif_di_src_passa_dal_ritmo():
    assert _scoperte_in_src() == []


def test_tool_13f_passa_dal_ritmo_e_usa_il_contatto_vero(monkeypatch):
    from bellomberg.agents import agent_tools
    eventi = []
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "contatto.prova@example.org")
    monkeypatch.setattr("bellomberg.storage.negozi_privati.carica_istituzioni",
                        lambda: {"origine": "file", "motivo": None, "istituzioni": {"cik": {"acme fund": "0009990001"}}})
    monkeypatch.setattr(sec_edgar, "attendi_sec", lambda **k: eventi.append("ritmo"))

    class R:
        status_code = 200

        def json(self):
            return {"name": "ACME FUND", "filings": {"recent": {"form": [], "filingDate": [], "accessionNumber": []}}}

    def get(url, headers=None, **k):
        eventi.append(("rete", headers.get("User-Agent")))
        return R()
    monkeypatch.setattr(agent_tools._req, "get", get)
    agent_tools.tool_get_13f_filing("acme fund")
    assert eventi[0] == "ritmo" and eventi[1][0] == "rete"
    assert "contatto.prova@example.org" in eventi[1][1] and "finance.research" not in eventi[1][1]


def test_gleif_ritmo_contatto_e_tetto_di_byte(monkeypatch):
    from bellomberg.market_data import esef_sito
    eventi = []
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "contatto.prova@example.org")
    monkeypatch.setattr(esef_sito, "attendi_gleif", lambda **k: eventi.append("ritmo"), raising=False)

    class R:
        status_code = 200
        headers = {"Content-Type": "application/vnd.api+json"}

        def __init__(self, corpo):
            self.corpo = corpo

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            for i in range(0, len(self.corpo), n):
                yield self.corpo[i:i + n]

        def json(self):
            return json.loads(self.corpo)

        def close(self):
            pass

    import json
    import pytest
    import requests
    corpo = [b'{"data": []}']

    def get(url, headers=None, **k):
        eventi.append(("rete", headers.get("User-Agent")))
        return R(corpo[0])
    monkeypatch.setattr(requests, "get", get)
    assert esef_sito._gleif_scarica("Acme SA") == []
    assert eventi[0] == "ritmo" and "contatto.prova@example.org" in eventi[1][1]
    corpo[0] = b'{"data": [' + b'{"x": 1},' * 400_000 + b'{"x": 1}]}'
    with pytest.raises(ValueError, match="GLEIF"):
        esef_sito._gleif_scarica("Acme SA")
