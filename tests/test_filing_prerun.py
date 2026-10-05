import sqlite3
import threading
import time

from bellomberg.market_data.filing_prerun import AggiornamentoPreRun
from bellomberg.storage.filing_store import FilingStore, RunAlreadyActive, RunNotDue, ensure_schema


class _Store:
    def __init__(self, dovuti, attivi=()):
        self._dovuti, self._attivi = list(dovuti), set(attivi)
        self.finiti = {}

    def next_due(self, now=None):
        return [{"ticker": t} for t in self._dovuti]

    def get_profile(self, t):
        return {"ticker": t, "enabled": True}

    def list_runs(self, t, limit=20):
        if t in self._attivi:
            return [{"status": "running"}]
        return [self.finiti.get(t, {"status": "ok", "reason": None})]


class _Svc:
    def __init__(self, store, ritardi, errori=(), blocchi=None):
        self.store, self.ritardi, self.errori = store, ritardi, set(errori)
        self.chiamate = []
        # Cantiere zero rossi 05/10 (TIMING): un titolo «lento» si ferma su un evento che il test
        # libera alla fine (in corso per costruzione, non per un sleep che il carico puo' battere).
        self.blocchi = dict(blocchi or {})
        self.partito = {t: threading.Event() for t in self.blocchi}

    def run_programmato(self, t):
        self.chiamate.append(t)
        if t in self.store._attivi:
            raise RunAlreadyActive("run già attivo")
        if t in self.blocchi:
            self.partito[t].set()
            self.blocchi[t].wait(30)
        time.sleep(self.ritardi.get(t, 0))
        return {"ticker": t, "status": "errore" if t in self.errori else "ok", "reason": "rete giù" if t in self.errori else None}


class _Orologio:
    """Orologio monotonico finto iniettato (AggiornamentoPreRun(orologio=...)): l'istante lo
    decide il test, non il carico della macchina."""

    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _attese_registrate(monkeypatch):
    """Registra il timeout che esiti() calcola per wait(); poi controlla i futuri SENZA attendere
    (il test ha gia' atteso, con una rete larga, quelli che devono essere pronti)."""
    import bellomberg.market_data.filing_prerun as modulo
    from concurrent.futures import wait as vera
    registrate = []

    def wait(fs, timeout=None):
        registrate.append(timeout)
        return vera(fs, timeout=0)
    monkeypatch.setattr(modulo, "wait", wait)
    return registrate


def _pronti(agg, tickers):
    from concurrent.futures import wait as vera
    _, non_pronti = vera([agg._futuri[t] for t in tickers], timeout=10)
    assert not non_pronti, f"titoli veloci non conclusi in 10 s: {tickers}"


def test_scadenza_assoluta_dall_avvio_della_run(monkeypatch):
    # TIMING 05/10: prima `esiti()` cronometrato < 0,9 s e NOVA che doveva finire entro 0,5 s
    # reali (rosso possibile sotto carico). Ora orologio finto: la garanzia «si attende solo il
    # RESIDUO fino alla scadenza assoluta avvio+attesa_max» e' il timeout passato a wait(), esatto.
    attese = _attese_registrate(monkeypatch)
    store = _Store(["NOVA.DE", "LENTO.MI", "ERR.PA"])
    lento = threading.Event()
    svc = _Svc(store, {}, errori={"ERR.PA"}, blocchi={"LENTO.MI": lento})
    agg = AggiornamentoPreRun(svc, ["NOVA.DE", "LENTO.MI", "ERR.PA", "FRESCO.MI"], avvio_run=1000.0,
                              attesa_max_s=1.0, orologio=_Orologio(1000.5))  # partita mezzo secondo fa
    try:
        agg.avvia()
        _pronti(agg, ["NOVA.DE", "ERR.PA"])
        esiti = agg.esiti()
    finally:
        lento.set()
    assert attese == [0.5]                    # attende solo il residuo, non attesa_max_s da adesso
    assert esiti["NOVA.DE"]["stato"] == "aggiornato"
    assert esiti["FRESCO.MI"]["stato"] == "aggiornato"          # non dovuto
    assert esiti["LENTO.MI"] == {"stato": "non_aggiornato", "motivo": "aggiornamento oltre 1 s (in corso)"}
    assert esiti["ERR.PA"]["stato"] == "non_aggiornato" and "rete giù" in esiti["ERR.PA"]["motivo"]


def test_scadenza_gia_passata_non_attende(monkeypatch):
    # TIMING 05/10: prima < 0,3 s cronometrati; ora il timeout calcolato e' esattamente 0.
    attese = _attese_registrate(monkeypatch)
    store = _Store(["LENTO.MI"])
    lento = threading.Event()
    svc = _Svc(store, {}, blocchi={"LENTO.MI": lento})
    agg = AggiornamentoPreRun(svc, ["LENTO.MI"], avvio_run=1000.0, attesa_max_s=1.0, orologio=_Orologio(1005.0))
    try:
        agg.avvia()
        assert agg.esiti()["LENTO.MI"]["motivo"].startswith("aggiornamento oltre")
    finally:
        lento.set()
    assert attese == [0.0]


def _sonno_che_conclude(store, ticker):
    """Sonno finto: alla prima attesa il run attivo del backend si conclude (prima: Timer reale
    da 0,2-0,3 s contro una scadenza reale di 1 s, battibile dal carico)."""
    sonni = []

    def sonno(s):
        sonni.append(s)
        store._attivi.discard(ticker)
    return sonni, sonno


def test_run_gia_attivo_si_attende_quello_senza_secondo_run():
    store = _Store(["KORE.MI"], attivi={"KORE.MI"})
    svc = _Svc(store, {})
    sonni, sonno = _sonno_che_conclude(store, "KORE.MI")
    # scadenza larga (30 s) sull'orologio finto: l'unica attesa reale e' quella del thread
    agg = AggiornamentoPreRun(svc, ["KORE.MI"], avvio_run=1000.0, attesa_max_s=30.0,
                              orologio=_Orologio(1000.0), sonno=sonno)
    agg.avvia()
    assert agg.esiti()["KORE.MI"]["stato"] == "aggiornato"
    assert svc.chiamate == ["KORE.MI"]
    assert sonni == [0.5]                     # ha atteso il run gia' attivo (passo da 0,5 s)


def test_run_gia_attivo_concluso_in_errore_e_dichiarato():
    store = _Store(["KORE.MI"], attivi={"KORE.MI"})
    store.finiti["KORE.MI"] = {"status": "errore", "reason": "SEC 503"}
    sonni, sonno = _sonno_che_conclude(store, "KORE.MI")
    agg = AggiornamentoPreRun(_Svc(store, {}), ["KORE.MI"], avvio_run=1000.0, attesa_max_s=30.0,
                              orologio=_Orologio(1000.0), sonno=sonno)
    agg.avvia()
    assert agg.esiti()["KORE.MI"] == {"stato": "non_aggiornato", "motivo": "controllo in errore: SEC 503"}
    assert sonni == [0.5]


def test_run_attivo_del_backend_fuori_da_next_due_si_attende(tmp_path):
    """Archivio vero: next_due esclude i ticker con run attivo; il pre-run li attende comunque."""
    db = tmp_path / "f.db"
    with sqlite3.connect(db) as conn:
        ensure_schema(conn)
    store = FilingStore(db)
    store.set_profile("NOVA.DE", {"ticker": "NOVA.DE", "cik": "0009990001", "emittente_id": "CIK:0009990001", "tipo": "annuale", "lingua": "en",
                                  "perimetro": "consolidato",
                                  "verifica": {"lingua": "English", "tipo": "annual", "perimetro": "consolidated"},
                                  "sezioni": {"rischi": {"inizio": "Risk Factors", "fine": "Properties"}}},
                      interval_hours=24)
    run = store.start_run("NOVA.DE", "scheduled")       # il thread del backend
    assert store.next_due() == []

    class _Svc2:
        def __init__(self):
            self.store, self.chiamate = store, 0

        def run_programmato(self, t):
            self.chiamate += 1
            return self.store.start_run(t, "scheduled")  # solleva RunAlreadyActive

    svc = _Svc2()
    agg = AggiornamentoPreRun(svc, ["NOVA.DE"], avvio_run=time.monotonic(), attesa_max_s=0.6)
    agg.avvia()
    esito = agg.esiti()["NOVA.DE"]
    assert esito["stato"] == "non_aggiornato" and esito["motivo"].startswith("aggiornamento oltre")
    assert len(store.list_runs("NOVA.DE")) == 1                 # nessun secondo run
    store.claim_execution(run["id"])
    store.finish_run(run["id"], status="ok", reason=None, result={})


def test_run_non_dovuto_nel_claim_legge_l_ultimo_run():
    store = _Store(["NOVA.DE"])
    store.finiti["NOVA.DE"] = {"status": "parziale", "reason": None}

    class _Svc3(_Svc):
        def run_programmato(self, t):
            raise RunNotDue("non ancora dovuto")
    # TIMING 05/10: scadenza larga (30 s): il lavoro finisce subito, il test non misura la scadenza
    # (prima 1 s reale: il thread sotto carico poteva non concludere in tempo).
    agg = AggiornamentoPreRun(_Svc3(store, {}), ["NOVA.DE"], avvio_run=time.monotonic(), attesa_max_s=30.0)
    agg.avvia()
    assert agg.esiti()["NOVA.DE"]["stato"] == "aggiornato"


def test_senza_profilo_o_disabilitato_nessuna_voce():
    class _S(_Store):
        def get_profile(self, t):
            return None if t == "NUDO.MI" else {"ticker": t, "enabled": t != "SPENTO.MI"}
    agg = AggiornamentoPreRun(_Svc(_S([]), {}), ["NUDO.MI", "SPENTO.MI", "NOVA.DE"],
                              avvio_run=time.monotonic(), attesa_max_s=1.0)
    agg.avvia()
    assert agg.esiti() == {"NOVA.DE": {"stato": "aggiornato", "motivo": None}}


def test_eccezione_del_servizio_e_errore_dichiarato():
    store = _Store(["NOVA.DE"])

    class _Svc4(_Svc):
        def run_programmato(self, t):
            raise OSError("disco pieno")
    # TIMING 05/10: scadenza larga (30 s): il lavoro finisce subito, il test non misura la scadenza
    # (prima 1 s reale: il thread sotto carico poteva non concludere in tempo).
    agg = AggiornamentoPreRun(_Svc4(store, {}), ["NOVA.DE"], avvio_run=time.monotonic(), attesa_max_s=30.0)
    agg.avvia()
    e = agg.esiti()["NOVA.DE"]
    assert e["stato"] == "non_aggiornato" and e["motivo"].startswith("controllo in errore: OSError: disco pieno")


def test_archivio_rotto_non_blocca():
    class _Rotto:
        @property
        def store(self):
            raise FileNotFoundError("DB filing assente")
    agg = AggiornamentoPreRun(_Rotto(), ["NOVA.DE"], avvio_run=time.monotonic(), attesa_max_s=1.0)
    agg.avvia()
    assert agg.esiti()["NOVA.DE"]["motivo"].startswith("archivio filing")


def test_esiti_ripetuto_stesso_risultato():
    store = _Store(["NOVA.DE"])
    # TIMING 05/10: scadenza larga (30 s): il lavoro finisce subito, il test non misura la scadenza
    # (prima 1 s reale: il thread sotto carico poteva non concludere in tempo).
    agg = AggiornamentoPreRun(_Svc(store, {}), ["NOVA.DE"], avvio_run=time.monotonic(), attesa_max_s=30.0)
    agg.avvia()
    assert agg.esiti() == agg.esiti()


def test_esclusi_non_si_aggiornano_preferenze_illeggibili_niente_aggiornamento():
    store = _Store(["NOVA.DE", "KORE.MI"])
    svc = _Svc(store, {})
    # TIMING 05/10: scadenza larga (30 s): il lavoro finisce subito, il test non misura la scadenza
    # (prima 1 s reale: il thread sotto carico poteva non concludere in tempo).
    agg = AggiornamentoPreRun(svc, ["NOVA.DE", "KORE.MI"], avvio_run=time.monotonic(), attesa_max_s=30.0,
                              esclusi_fn=lambda: frozenset({"KORE.MI"}))
    agg.avvia()
    esiti = agg.esiti()
    assert svc.chiamate == ["NOVA.DE"] and "KORE.MI" not in esiti

    def rotte():
        raise ValueError("preferenze filing illeggibili")
    svc = _Svc(store, {})
    agg = AggiornamentoPreRun(svc, ["NOVA.DE", "KORE.MI"], avvio_run=time.monotonic(), attesa_max_s=1.0,
                              esclusi_fn=rotte)
    agg.avvia()
    esiti = agg.esiti()
    # Seguito revisione G1: prima si aggiornavano tutti (anche l'escluso), detto solo nel log.
    assert svc.chiamate == []
    assert {t: e["stato"] for t, e in esiti.items()} == {"NOVA.DE": "non_aggiornato", "KORE.MI": "non_aggiornato"}
    assert all("preferenze filing illeggibili" in e["motivo"] for e in esiti.values())


def test_chiudi_annulla_i_lavori_non_partiti_e_lascia_finire_quelli_in_corso():
    # Revisione finale M8: a fine run i futuri in coda si annullano (nessun run avviato),
    # quelli gia' partiti finiscono; esiti() dichiara gli annullati senza sollevare.
    store = _Store(["AAA.MI", "BBB.MI"])
    # TIMING 05/10: prima AAA dormiva 0,4 s e il test sperava che dopo sleep(0.1) fosse gia' partito
    # (sotto carico chiudi() poteva annullare anche AAA). Ora AAA si ferma su un evento e il test
    # attende il SEGNALE di partenza: AAA in corso e BBB in coda per costruzione.
    libera = threading.Event()
    svc = _Svc(store, {}, blocchi={"AAA.MI": libera})
    agg = AggiornamentoPreRun(svc, ["AAA.MI", "BBB.MI"], avvio_run=time.monotonic(), attesa_max_s=0.1,
                              max_workers=1, esclusi_fn=frozenset)
    agg.avvia()
    try:
        assert svc.partito["AAA.MI"].wait(10)        # AAA in corso, BBB in coda
        agg.chiudi()
        assert agg._futuri["BBB.MI"].cancelled()
        assert not agg._futuri["AAA.MI"].cancelled()  # quello in corso non si annulla
    finally:
        libera.set()
    agg._futuri["AAA.MI"].result(timeout=10)
    assert svc.chiamate == ["AAA.MI"]
    esiti = agg.esiti()
    assert esiti["BBB.MI"]["stato"] == "non_aggiornato"
    agg.chiudi()                                     # ripetibile, mai solleva
    AggiornamentoPreRun(svc, [], avvio_run=time.monotonic(), esclusi_fn=frozenset).chiudi()
