"""Outbox email: nessun doppio invio fra run, worker e processi (review 04, E1-E4).

Contratto: con una delivery_receipt il job non viene MAI rispedito dal worker e un esito
non conoscibile e' «uncertain». Decisione PM: con la ricevuta si ritentano SOLO gli errori
avvenuti prima della trasmissione (connessione, TLS, autenticazione, MAIL FROM/RCPT
rifiutati); dall'inizio del DATA in poi mai. Tutti i server SMTP qui sono finti (loopback
o classi), nessuna email vera.
"""
import json
import os
import smtplib
import socketserver
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

import pytest


def _sender(monkeypatch, outbox):
    import bellomberg.reporting.email_sender as mod
    monkeypatch.setattr(mod, "OUTBOX_DIR", outbox)
    monkeypatch.setattr(mod, "EMAIL_FROM", "sender@example.invalid")
    monkeypatch.setattr(mod, "EMAIL_PASSWORD", "synthetic-app-password")
    monkeypatch.setattr(mod, "EMAIL_TO", "pm@example.invalid")
    monkeypatch.setattr(mod, "email_configurata", lambda: True)
    monkeypatch.setattr(mod, "EMAIL_SEND_ATTEMPTS", 3)
    monkeypatch.setattr(mod, "EMAIL_RETRY_DELAY_SECONDS", 0)
    return mod


def _pdf(tmp_path):
    path = tmp_path / "memo.pdf"
    path.write_bytes(b"%PDF synthetic")
    return [str(path)]


class _CountingSMTP:
    """Fake SMTP_SSL: counts accepted messages; an optional hook runs inside send_message."""
    accepted = []
    during_send = None

    def __init__(self, *_a, **_k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def login(self, *_a):
        return (235, b"ok")

    def send_message(self, message):
        hook = type(self).during_send
        if hook is not None:
            type(self).during_send = None
            hook()
        type(self).accepted.append(message["Subject"])
        return {}


# --- E1: il worker non tocca un job con ricevuta mentre la run lo sta inviando ----------

def test_worker_tick_during_receipt_send_does_not_send_twice(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = lambda: mod.flush_email_outbox()
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    receipt = {}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="Weekly ZZTEST",
                                          delivery_receipt=receipt) is True
    assert _CountingSMTP.accepted == ["Weekly ZZTEST"]
    assert receipt["email_status"] == "accepted"


def test_worker_tick_during_retry_pause_does_not_send_twice(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    calls = []

    class FlakyConnect(_CountingSMTP):
        def __init__(self, *a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("synthetic outage before login")

    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", FlakyConnect)
    # The pause between attempts is where the API worker used to find the job.
    monkeypatch.setattr(mod.time, "sleep", lambda _s: mod.flush_email_outbox())
    receipt = {}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="Weekly ZZTEST",
                                          delivery_receipt=receipt) is True
    assert _CountingSMTP.accepted == ["Weekly ZZTEST"]


def test_receipt_job_is_marked_at_queue_time_and_never_flushed(tmp_path, monkeypatch):
    """Run killed between SMTP acceptance and job update: the orphan must stay unsent."""
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    seen = {}

    def snapshot_job():
        job_file, = (tmp_path / "outbox").glob("*.job.json")
        seen.update(json.loads(job_file.read_text(encoding="utf-8")))
        seen["path"] = job_file

    _CountingSMTP.during_send = snapshot_job
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="Weekly ZZTEST",
                                          delivery_receipt={}) is True
    assert seen["receipt_tracked"] is True and seen.get("expires_at")
    # Rebuild the orphan exactly as it was on disk while the run was transmitting.
    orphan = {k: v for k, v in seen.items() if k != "path"}
    seen["path"].write_text(json.dumps(orphan), encoding="utf-8")
    assert mod.flush_email_outbox() == 0
    assert _CountingSMTP.accepted == ["Weekly ZZTEST"]


def test_legacy_job_without_marking_is_never_sent(tmp_path, monkeypatch):
    """Jobs written by 64ee17e carry no receipt flag: every caller had a receipt."""
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    import base64
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    msg = MIMEText("x")
    msg["Subject"] = "legacy ZZTEST"
    (outbox / "email_legacy.job.json").write_text(json.dumps({
        "job_id": "email_legacy", "created_at": "2026-10-01T10:00:00",
        "message_b64": base64.b64encode(msg.as_bytes()).decode("ascii"),
        "attempts": 0, "last_error": None}), encoding="utf-8")
    assert mod.flush_email_outbox() == 0
    assert _CountingSMTP.accepted == []
    job = json.loads((outbox / "email_legacy.job.json").read_text(encoding="utf-8"))
    assert job["blocked"] is True and "legacy" in job["blocked_reason"].lower()


_HOLDER = r"""
import sys
from pathlib import Path
import bellomberg.reporting.email_sender as m
m.OUTBOX_DIR = Path(sys.argv[1])
claim = m._acquire_claim(sys.argv[2])
print("HELD" if claim is not None else "BUSY", flush=True)
sys.stdin.read()
"""


def _queue_plain(mod, subject):
    msg = MIMEText("body")
    msg["Subject"] = subject
    job_path, claim = mod._queue_message(msg, receipt_tracked=False)
    mod._release_claim(claim, job_path.name[:-len(".job.json")])
    return job_path


def test_claim_is_exclusive_across_processes_and_released_when_the_holder_dies(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    job_path = _queue_plain(mod, "plain ZZTEST")
    job_id = job_path.name[:-len(".job.json")]
    env = dict(os.environ, PYTHONPATH=str(Path(mod.__file__).resolve().parents[2]))
    child = subprocess.Popen([sys.executable, "-c", _HOLDER, str(tmp_path / "outbox"), job_id],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, text=True, encoding="utf-8", errors="replace")
    try:
        line = child.stdout.readline().strip()
        assert line == "HELD", child.stderr.read()
        assert mod.flush_email_outbox() == 0          # another live process owns the job
        assert _CountingSMTP.accepted == []
    finally:
        child.kill()
        child.wait(timeout=10)
    assert mod.flush_email_outbox() == 1              # the OS released the dead holder's claim
    assert _CountingSMTP.accepted == ["plain ZZTEST"]


def test_holder_died_during_data_is_blocked_as_uncertain_not_resent(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    job_path = _queue_plain(mod, "plain ZZTEST")
    job_id = job_path.name[:-len(".job.json")]
    mod._write_attempt_marker(job_id, "data")         # what a killed sender leaves behind
    assert mod.flush_email_outbox() == 0
    assert _CountingSMTP.accepted == []
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["blocked"] is True and job["blocked_reason"].startswith("uncertain")


def test_holder_died_before_data_is_retried_without_receipt(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    job_path = _queue_plain(mod, "plain ZZTEST")
    mod._write_attempt_marker(job_path.name[:-len(".job.json")], "pre_data")
    assert mod.flush_email_outbox() == 1
    assert _CountingSMTP.accepted == ["plain ZZTEST"]
    assert not list((tmp_path / "outbox").iterdir())


# --- E2: invio riuscito + pulizia fallita = inviato -------------------------------------

def test_sent_message_with_failed_cleanup_is_reported_sent_and_never_resent(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    real_unlink = Path.unlink
    blocked = {"on": True}

    def locked_unlink(self, *a, **k):
        if blocked["on"] and self.name.endswith(".job.json"):
            raise PermissionError(32, "synthetic: file opened by another process")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    receipt = {}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="Weekly ZZTEST",
                                          delivery_receipt=receipt) is True
    assert receipt["email_status"] == "accepted"
    assert "PermissionError" in receipt["outbox_cleanup_error"]
    blocked["on"] = False
    # The sent marker, not the job file, decides: the next tick only finishes the cleanup.
    assert mod.flush_email_outbox() == 0
    assert _CountingSMTP.accepted == ["Weekly ZZTEST"]
    assert not list((tmp_path / "outbox").glob("*.job.json"))


def test_sent_without_receipt_and_failed_cleanup_is_not_resent(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    real_unlink = Path.unlink
    blocked = {"on": True}

    def locked_unlink(self, *a, **k):
        if blocked["on"] and self.name.endswith(".job.json"):
            raise PermissionError(32, "synthetic: file opened by another process")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="Plain ZZTEST") is True
    blocked["on"] = False
    assert mod.flush_email_outbox() == 0
    assert _CountingSMTP.accepted == ["Plain ZZTEST"]


def test_deliver_once_never_records_not_sent_after_smtp_acceptance(tmp_path):
    from bellomberg.agents import weekly_lifecycle as wl
    from hashlib import sha256
    pdf = tmp_path / "memo.pdf"
    pdf.write_bytes(b"%PDF synthetic")
    path = str(pdf.resolve())

    class Store:
        def __init__(self):
            self.state = {}
            self.saved = {"decisions_finalized": {"ids": []},
                          "artifact_bundle": {"artifacts": [
                              {"path": path, "sha256": sha256(pdf.read_bytes()).hexdigest()}]}}

        def status(self):
            return dict(self.state)

        def update(self, **kw):
            self.state.update(kw)

        def get(self, key):
            return self.saved.get(key)

    class Module:
        @staticmethod
        def _send_weekly_email(attachments, *, body_extra, delivery, **_k):
            # Legacy outcome of E2: SMTP accepted but the function said False.
            delivery.update(email_status="accepted", smtp_state="accepted")
            return False

    store = Store()
    delivery = {}
    assert wl.deliver_once(store, Module, [path], body_extra="", delivery=delivery,
                           send_email=True) is False
    assert store.state["email_delivery"]["state"] == "uncertain"
    assert store.state["delivery_status"] == "uncertain"
    assert delivery["email_status"] == "uncertain"


# --- Decisione PM: con ricevuta, retry solo prima della trasmissione --------------------

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        srv = self.server
        srv.connections += 1
        self.wfile.write(b"220 fake.invalid ESMTP\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode("ascii", "replace").strip().upper()
            if cmd.startswith(("EHLO", "HELO")):
                if srv.plan.get("ehlo") == "drop_once" and not srv.dropped:
                    srv.dropped = True
                    return
                self.wfile.write(b"250 fake.invalid\r\n")
            elif cmd.startswith("MAIL"):
                self.wfile.write(b"250 ok\r\n")
            elif cmd.startswith("RCPT"):
                if srv.plan.get("rcpt") == "drop_once" and not srv.dropped:
                    srv.dropped = True
                    return
                if srv.plan.get("rcpt") == "refuse":
                    self.wfile.write(b"550 no such user\r\n")
                else:
                    self.wfile.write(b"250 ok\r\n")
            elif cmd == "DATA":
                if srv.plan.get("data") == "cmd554":
                    self.wfile.write(b"554 no valid recipients\r\n")
                    continue
                self.wfile.write(b"354 go\r\n")
                if srv.plan.get("data") == "drop":
                    self.rfile.readline()
                    return
                while self.rfile.readline() not in (b".\r\n", b""):
                    pass
                if srv.plan.get("data") in ("451", "554"):
                    self.wfile.write(srv.plan["data"].encode("ascii") + b" rejected by fake\r\n")
                else:
                    srv.accepted += 1
                    self.wfile.write(b"250 queued\r\n")
            elif cmd == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


@pytest.fixture
def loop_smtp(monkeypatch):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.plan, server.connections, server.accepted, server.dropped = {}, 0, 0, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    class LoopSMTP(smtplib.SMTP):
        """The real smtplib client against a loopback fake; login is skipped (no AUTH)."""
        def __init__(self, *_a, **_k):
            super().__init__("127.0.0.1", port, timeout=5)

        def login(self, *_a):
            return (235, b"ok")

    import bellomberg.reporting.email_sender as mod
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", LoopSMTP)
    yield server
    server.shutdown()
    server.server_close()


def test_pm_rule_receipt_retries_a_drop_before_data(tmp_path, monkeypatch, loop_smtp):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    loop_smtp.plan = {"rcpt": "drop_once"}
    receipt = {}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="ZZTEST",
                                          delivery_receipt=receipt) is True
    assert loop_smtp.connections == 2 and loop_smtp.accepted == 1
    assert receipt["email_status"] == "accepted"


def test_pm_rule_receipt_rcpt_refusal_is_failed_not_uncertain(tmp_path, monkeypatch, loop_smtp):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    loop_smtp.plan = {"rcpt": "refuse"}
    receipt = {}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="ZZTEST",
                                          delivery_receipt=receipt) is False
    assert receipt["email_status"] == "failed"
    assert receipt["smtp_state"] == "envelope"
    assert loop_smtp.accepted == 0


@pytest.mark.parametrize("data,status,code", [("451", "failed", "451"), ("554", "failed", "554"),
                                              ("cmd554", "failed", "554"), ("drop", "uncertain", None)])
def test_pm_rule_receipt_never_retries_once_data_started(tmp_path, monkeypatch, loop_smtp, data,
                                                         status, code):
    """PM 04/10: an explicit server rejection at the DATA command or at the end of the content
    (4xx or 5xx) = failed, the message was not delivered; only a lost connection, timeout or
    unreadable reply after DATA started = uncertain. With a receipt neither is retried
    automatically and the worker never sends the job."""
    mod = _sender(monkeypatch, tmp_path / "outbox")
    loop_smtp.plan = {"data": data}
    receipt = {}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="ZZTEST",
                                          delivery_receipt=receipt) is False
    assert receipt["email_status"] == status
    assert receipt["smtp_state"] == ("data_rejected" if status == "failed" else "sending")
    if code:
        assert receipt["email_error"].startswith("SMTPDataError " + code)
    assert loop_smtp.accepted == 0
    assert loop_smtp.connections == 1
    assert mod.flush_email_outbox() == 0 and loop_smtp.connections == 1


# --- E3/E4: scadenza, pulizia, configurazione ------------------------------------------

def test_expired_job_is_blocked_and_declared_not_sent_late(tmp_path, monkeypatch, capsys):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    job_path = _queue_plain(mod, "late ZZTEST")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    job_path.write_text(json.dumps(job), encoding="utf-8")
    assert mod.flush_email_outbox() == 0
    assert _CountingSMTP.accepted == []
    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["blocked"] is True and job["blocked_reason"].startswith("expired")
    assert "scaduto" in capsys.readouterr().out


def test_prune_removes_old_blocked_jobs_and_declares_them(tmp_path, monkeypatch, capsys):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    queued = _queue_plain(mod, "old ZZTEST")
    job = json.loads(queued.read_text(encoding="utf-8"))
    # The age comes from the job id (review G5, R5: prune never opens the JSON).
    old_moment = datetime.now() - timedelta(days=60)
    job["job_id"] = "email_" + old_moment.strftime("%Y%m%d_%H%M%S") + "_" + "0" * 32
    job.update(blocked=True, blocked_reason="failed: synthetic")
    job_path = queued.with_name(job["job_id"] + ".job.json")
    job_path.write_text(json.dumps(job), encoding="utf-8")
    queued.unlink()
    fresh = _queue_plain(mod, "fresh ZZTEST")
    result = mod.prune_email_outbox()
    assert result["removed"] == [job["job_id"]] and result["errors"] == []
    assert not job_path.exists() and fresh.exists()
    assert not list((tmp_path / "outbox").glob(job["job_id"] + ".*"))
    assert job["job_id"] in capsys.readouterr().out


_BAD_ENV = r"""
import json, sys
from pathlib import Path
import bellomberg.reporting.email_sender as m
m.OUTBOX_DIR = Path(sys.argv[1])
m.email_configurata = lambda: True
worker = m.EmailOutboxWorker(interval_seconds=0.01)
status = worker.start()
receipt = {}
sent = m.invia_email_multi_allegati([sys.argv[2]], oggetto="ZZTEST", delivery_receipt=receipt)
print("RESULT " + json.dumps({"error": m.email_config_error(), "worker": status,
                              "thread": worker.thread is not None, "sent": sent, "receipt": receipt}))
"""


def test_invalid_email_env_is_declared_without_breaking_the_import(tmp_path):
    import bellomberg.reporting.email_sender as mod
    pdf = _pdf(tmp_path)[0]
    env = dict(os.environ, PYTHONPATH=str(Path(mod.__file__).resolve().parents[2]),
               EMAIL_SEND_ATTEMPTS="tre", BELLOMBERG_DATA_DIR=str(tmp_path / "data"))
    run = subprocess.run([sys.executable, "-c", _BAD_ENV, str(tmp_path / "outbox"), pdf],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env, timeout=120)
    assert run.returncode == 0, run.stderr
    line = next(l for l in run.stdout.splitlines() if l.startswith("RESULT "))
    result = json.loads(line[len("RESULT "):])
    assert "EMAIL_SEND_ATTEMPTS" in result["error"]
    assert result["worker"]["running"] is False and "EMAIL_SEND_ATTEMPTS" in result["worker"]["reason"]
    assert result["thread"] is False
    assert result["sent"] is False
    assert result["receipt"]["email_status"] == "failed"
    assert "EMAIL_SEND_ATTEMPTS" in result["receipt"]["email_error"]
    assert not (tmp_path / "outbox").exists() or not list((tmp_path / "outbox").glob("*.job.json"))


def test_smtp_credentials_never_enter_the_job(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    job_path = _queue_plain(mod, "cred ZZTEST")
    raw = job_path.read_text(encoding="utf-8")
    assert "synthetic-app-password" not in raw
    import base64
    assert b"synthetic-app-password" not in base64.b64decode(json.loads(raw)["message_b64"])


@pytest.mark.parametrize("code,status", [(550, "failed"), (451, "failed"), (-1, "uncertain")])
def test_pm_rule_data_rejection_from_a_client_without_phase_hooks(tmp_path, monkeypatch, code, status):
    """Same case as test_trade_idea_api_delivery [failed]: a fake send_message raising
    SMTPDataError. A readable 4xx/5xx is a rejection (failed); code -1 (unparsable reply)
    stays uncertain."""
    mod = _sender(monkeypatch, tmp_path / "outbox")
    calls = []

    class RejectingSMTP(_CountingSMTP):
        def send_message(self, _message):
            calls.append(1)
            raise smtplib.SMTPDataError(code, b"synthetic DATA reply")

    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", RejectingSMTP)
    receipt = {}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="ZZTEST",
                                          delivery_receipt=receipt) is False
    assert receipt["email_status"] == status
    assert len(calls) == 1
    assert mod.flush_email_outbox() == 0 and len(calls) == 1


# --- Review G5 (REV_G5.md), seguito 2 ---------------------------------------------------

class _AcceptThenDrop(socketserver.StreamRequestHandler):
    """The server queues the message and the connection drops before the 250."""
    def handle(self):
        srv = self.server
        self.wfile.write(b"220 fake.invalid ESMTP\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode("ascii", "replace").strip().upper()
            if cmd == "DATA":
                self.wfile.write(b"354 go\r\n")
                while self.rfile.readline() not in (b".\r\n", b""):
                    pass
                srv.accepted += 1
                return
            elif cmd == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


@pytest.fixture
def drop_smtp(monkeypatch):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _AcceptThenDrop)
    server.daemon_threads = True
    server.accepted = 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    class LoopSMTP(smtplib.SMTP):
        def __init__(self, *_a, **_k):
            super().__init__("127.0.0.1", port, timeout=5)

        def login(self, *_a):
            return (235, b"ok")

    import bellomberg.reporting.email_sender as mod
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", LoopSMTP)
    yield server
    server.shutdown()
    server.server_close()


def test_r1_without_receipt_a_drop_after_data_is_ambiguous_and_never_resent(tmp_path, monkeypatch,
                                                                             drop_smtp):
    """Review G5 R1: measured 6 copies (3 inline + 3 from the worker) before the fix."""
    mod = _sender(monkeypatch, tmp_path / "outbox")
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="ZZTEST") is False
    assert drop_smtp.accepted == 1
    assert mod.flush_email_outbox() == 0
    assert drop_smtp.accepted == 1
    job, = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "outbox").glob("*.job.json")]
    assert job["blocked"] is True and job["blocked_reason"].startswith("uncertain")


def test_r1_without_receipt_an_explicit_4xx_data_rejection_is_still_retried(tmp_path, monkeypatch,
                                                                            loop_smtp):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    loop_smtp.plan = {"data": "451"}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="ZZTEST") is False
    assert loop_smtp.connections == 3 and loop_smtp.accepted == 0


@pytest.mark.parametrize("sender", ["alive", "dead_before_sidecar_pass"])
def test_r2_prune_keeps_the_markers_of_a_job_born_after_its_listing(tmp_path, monkeypatch, sender):
    """alive: the claim protects the markers; dead: the claim is free and only the re-check
    that the job still exists keeps the 'data' marker (review G5, R2)."""
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    (tmp_path / "outbox").mkdir()
    holder = {}
    real_glob = Path.glob

    def glob(self, pattern):
        result = list(real_glob(self, pattern))
        if pattern == "*.job.json" and not holder:
            msg = MIMEText("x")
            msg["Subject"] = "late ZZTEST"
            job_path, claim = mod._queue_message(msg, receipt_tracked=False)
            job_id = job_path.name[:-len(".job.json")]
            mod._write_attempt_marker(job_id, "data")      # a live sender inside DATA
            if sender != "alive":
                mod._release_claim(claim, job_id)          # it dies before the sidecar pass
                claim = None
            holder.update(claim=claim, job_id=job_id)
        return iter(result)

    monkeypatch.setattr(Path, "glob", glob)
    mod.prune_email_outbox()
    monkeypatch.setattr(Path, "glob", real_glob)
    assert mod._sidecar(holder["job_id"], "attempt").exists()
    if holder["claim"] is not None:
        mod._release_claim(holder["claim"], holder["job_id"])  # the sender dies inside DATA
    assert mod.flush_email_outbox() == 0
    assert _CountingSMTP.accepted == []


def test_r3_sent_marker_and_job_unlink_both_failing_never_resend(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    real_unlink, real_write = Path.unlink, Path.write_text
    on = {"x": True}

    def unlink(self, *a, **k):
        if on["x"] and self.name.endswith(".job.json"):
            raise PermissionError(32, "synthetic")
        return real_unlink(self, *a, **k)

    def write_text(self, *a, **k):
        if on["x"] and self.name.endswith(".sent"):
            raise PermissionError(32, "synthetic")
        return real_write(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(Path, "write_text", write_text)
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="Plain ZZTEST") is True
    on["x"] = False
    assert mod.flush_email_outbox() == 0
    assert _CountingSMTP.accepted == ["Plain ZZTEST"]
    assert not list((tmp_path / "outbox").glob("*.job.json"))


def test_r4_a_claim_file_that_cannot_be_opened_is_busy_not_a_crash(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _CountingSMTP.accepted = []
    _CountingSMTP.during_send = None
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _CountingSMTP)
    first = _queue_plain(mod, "A ZZTEST")
    _queue_plain(mod, "B ZZTEST")
    stuck = mod._job_id_of(first) + ".claim"
    real_open = os.open

    def open_(path, *a, **k):
        if str(path).endswith(stuck):
            raise PermissionError(13, "synthetic delete pending")
        return real_open(path, *a, **k)

    monkeypatch.setattr(mod.os, "open", open_)
    assert mod._acquire_claim(mod._job_id_of(first)) is None
    assert mod.flush_email_outbox() == 1                  # the next job is not skipped
    assert _CountingSMTP.accepted == ["B ZZTEST"]
    assert mod.prune_email_outbox()["errors"] == []


def test_r5_prune_never_reads_a_job_file(tmp_path, monkeypatch):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    _queue_plain(mod, "fresh ZZTEST")
    reads = []
    real_read = Path.read_text

    def read_text(self, *a, **k):
        if self.name.endswith(".job.json"):
            reads.append(self.name)
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    real_open = open

    def open_(file, *a, **k):
        if str(file).endswith(".job.json"):
            reads.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr("builtins.open", open_)
    mod.prune_email_outbox(now=datetime.now(timezone.utc) + timedelta(days=30))
    assert reads == []
    assert not list((tmp_path / "outbox").glob("*.job.json"))


def test_r6_an_error_between_login_and_mail_is_before_transmission(tmp_path, monkeypatch, loop_smtp):
    mod = _sender(monkeypatch, tmp_path / "outbox")
    loop_smtp.plan = {"ehlo": "drop_once"}
    receipt = {}
    assert mod.invia_email_multi_allegati(_pdf(tmp_path), oggetto="ZZTEST",
                                          delivery_receipt=receipt) is True
    assert loop_smtp.connections == 2 and loop_smtp.accepted == 1
    assert receipt["email_status"] == "accepted"


def test_r7_health_declares_the_outbox_worker_state(monkeypatch):
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api as api
    client = TestClient(api.app, base_url="http://127.0.0.1:8765")
    monkeypatch.delattr(api.app.state, "email_outbox", raising=False)
    assert client.get("/health").json()["email_outbox"] == {"running": False,
                                                             "reason": "not_started"}
    reason = "configurazione email non valida: EMAIL_SEND_ATTEMPTS: valore non valido 'tre'"
    monkeypatch.setattr(api.app.state, "email_outbox", {"running": False, "reason": reason},
                        raising=False)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["email_outbox"] == {"running": False, "reason": reason}


_WORKER_CHILD = r"""
import sys
from pathlib import Path
import bellomberg.reporting.email_sender as m
m.OUTBOX_DIR = Path(sys.argv[1])
m.EMAIL_FROM, m.EMAIL_PASSWORD, m.EMAIL_TO = "a@example.invalid", "pw", "b@example.invalid"
sent = []
class S:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def login(self, *a): return (235, b"ok")
    def send_message(self, msg):
        sent.append(msg["Subject"])
        return {}
m.smtplib.SMTP_SSL = S
delivered = m.flush_email_outbox()
print("RESULT", delivered, len(sent), flush=True)
"""


def test_receipt_job_is_never_sent_by_a_worker_in_another_process(tmp_path, monkeypatch):
    """Second guard on receipt_tracked, different class: the API worker is another process."""
    mod = _sender(monkeypatch, tmp_path / "outbox")
    msg = MIMEText("x")
    msg["Subject"] = "receipt ZZTEST"
    job_path, claim = mod._queue_message(msg, receipt_tracked=True)
    mod._release_claim(claim, mod._job_id_of(job_path))   # the run died: no live owner
    env = dict(os.environ, PYTHONPATH=str(Path(mod.__file__).resolve().parents[2]))
    run = subprocess.run([sys.executable, "-c", _WORKER_CHILD, str(tmp_path / "outbox")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env, timeout=120)
    assert run.returncode == 0, run.stderr
    line = next(l for l in run.stdout.splitlines() if l.startswith("RESULT "))
    assert line == "RESULT 0 0"
    assert job_path.exists()
