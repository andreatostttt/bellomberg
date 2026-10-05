"""REV_G2a R-1: un server «a gocce» teneva fermo il download oltre ogni timeout (requests
applica il timeout a ogni lettura, non al download). Server finto su 127.0.0.1: nessuna rete
esterna. Il download ora ha una scadenza TOTALE e la dichiara (TimeoutError nel motivo)."""
import socket
import threading
import time

from bellomberg.market_data import lettore_trimestrali as lt


def _server(passo, n_byte=40):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)

    def servi():
        conn, _ = s.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\nContent-Length: %d\r\n\r\n" % n_byte)
        for _ in range(n_byte):
            time.sleep(passo)
            try:
                conn.sendall(b"x")
            except OSError:
                break
        conn.close()
        s.close()
    threading.Thread(target=servi, daemon=True).start()
    return f"http://127.0.0.1:{s.getsockname()[1]}/doc.pdf"


def _scarica(url, tmp_path, timeout=2, **kw):
    t0 = time.monotonic()
    esito = lt.scarica_documento(url, str(tmp_path / "dest"), timeout=timeout, **kw)
    return esito, time.monotonic() - t0


def test_gocce_piu_rapide_del_timeout_per_lettura_fermate_dalla_scadenza_totale(tmp_path):
    esito, durata = _scarica(_server(0.3), tmp_path, tempo_max_s=2)
    assert esito["stato"] == "errore" and "TimeoutError" in esito["motivo"] and "tempo massimo" in esito["motivo"]
    assert durata < 6, durata
    assert not (tmp_path / "dest").exists()  # nessun file parziale, nessuna cartella lasciata


def test_goccia_piu_lenta_di_una_lettura_la_recv_in_corso_viene_sbloccata(tmp_path):
    esito, durata = _scarica(_server(1.5, n_byte=10), tmp_path, tempo_max_s=2)
    assert esito["stato"] == "errore" and "TimeoutError" in esito["motivo"]
    assert durata < 6, durata


def test_default_della_scadenza_totale_dichiarato():
    assert lt.TEMPO_MAX_DOWNLOAD_S > 0 and lt.TEMPO_MAX_DOWNLOAD_ESEF_S >= lt.TEMPO_MAX_DOWNLOAD_S


def test_download_normale_entro_la_scadenza(tmp_path):
    esito, _ = _scarica(_server(0.0, n_byte=5), tmp_path, tempo_max_s=10)
    assert esito["stato"] == "ok" and esito["bytes"] == 5


def test_corpo_gzip_decodificato_come_prima(tmp_path):
    import gzip
    import hashlib
    corpo = ("".join(f"riga {i} del documento sintetico\n" for i in range(20000))).encode()
    compresso = gzip.compress(corpo)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)

    def servi():
        conn, _ = s.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Encoding: gzip\r\n"
                     b"Content-Length: %d\r\n\r\n" % len(compresso) + compresso)
        conn.close()
        s.close()
    threading.Thread(target=servi, daemon=True).start()
    esito, _ = _scarica(f"http://127.0.0.1:{s.getsockname()[1]}/doc.txt", tmp_path, tempo_max_s=20)
    assert esito["stato"] == "ok" and esito["bytes"] == len(corpo)
    assert esito["sha256"] == hashlib.sha256(corpo).hexdigest()


def test_lettura_bloccata_entro_il_timeout_per_lettura_tagliata_dal_timer(tmp_path):
    # goccia ogni 5 s, timeout per lettura 10 s: senza il timer la lettura in corso finisce solo
    # al primo byte (5 s); col timer la risposta si chiude allo scadere dei 2 s totali
    esito, durata = _scarica(_server(5.0, n_byte=4), tmp_path, timeout=10, tempo_max_s=2)
    assert esito["stato"] == "errore" and "TimeoutError" in esito["motivo"]
    assert durata < 4, durata
