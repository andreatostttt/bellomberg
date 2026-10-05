import pytest
import smtplib
import threading
import time


def _configure_sender(monkeypatch, tmp_path):
    import bellomberg.reporting.email_sender as mod

    monkeypatch.setattr(mod, "OUTBOX_DIR", tmp_path)
    monkeypatch.setattr(mod, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(mod, "EMAIL_PASSWORD", "app-password-for-test")
    monkeypatch.setattr(mod, "EMAIL_TO", "recipient@example.com")
    monkeypatch.setattr(mod, "email_configurata", lambda: True)
    monkeypatch.setattr(mod, "EMAIL_SEND_ATTEMPTS", 3)
    monkeypatch.setattr(mod, "EMAIL_SMTP_TIMEOUT", 120)
    monkeypatch.setattr(mod, "EMAIL_RETRY_DELAY_SECONDS", 0)
    return mod


def _attachments(tmp_path):
    memo = tmp_path / "memo.pdf"
    appendix = tmp_path / "appendix.pdf"
    memo.write_bytes(b"memo")
    appendix.write_bytes(b"appendix")
    return [str(memo), str(appendix)]


class _FakeSMTP:
    attempts = 0
    timeouts = []
    outcomes = []

    def __init__(self, *_args, **kwargs):
        self.timeouts.append(kwargs["timeout"])

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def login(self, *_args):
        return (235, b"2.7.0 Accepted")

    def send_message(self, _message):
        type(self).attempts += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise outcome
        return {}


def test_multi_attachment_email_retries_after_transient_disconnect(tmp_path, monkeypatch):
    mod = _configure_sender(monkeypatch, tmp_path / "outbox")
    _FakeSMTP.attempts = 0
    _FakeSMTP.timeouts = []
    _FakeSMTP.outcomes = [None]
    logins = []

    class DropBeforeTransmission(_FakeSMTP):
        # Review G5 R1: a drop inside send_message on a client whose SMTP phase cannot be
        # observed is ambiguous and never retried; the transient retry is proven before
        # transmission (lost connection at login), where the server accepted nothing.
        def login(self, *_args):
            logins.append(True)
            if len(logins) == 1:
                raise smtplib.SMTPServerDisconnected("drop")
            return (235, b"ok")

    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", DropBeforeTransmission)

    sent = mod.invia_email_multi_allegati(_attachments(tmp_path), oggetto="Retry test")

    assert sent is True
    assert len(logins) == 2 and DropBeforeTransmission.attempts == 1
    assert _FakeSMTP.timeouts == [120, 120]
    assert not list((tmp_path / "outbox").glob("*.job.json"))


def test_authentication_failure_is_not_retried(tmp_path, monkeypatch):
    mod = _configure_sender(monkeypatch, tmp_path / "outbox")
    attempts = []

    class AuthFailSMTP(_FakeSMTP):
        def login(self, *_args):
            attempts.append(True)
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", AuthFailSMTP)

    sent = mod.invia_email_multi_allegati(_attachments(tmp_path), oggetto="Auth test")

    assert sent is False
    assert len(attempts) == 1
    assert list((tmp_path / "outbox").glob("*.job.json"))


def test_permanent_failure_is_blocked_from_future_worker_flushes(tmp_path, monkeypatch):
    mod = _configure_sender(monkeypatch, tmp_path / "outbox")
    attempts = []

    class AuthFailSMTP(_FakeSMTP):
        def login(self, *_args):
            attempts.append(True)
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", AuthFailSMTP)
    assert mod.invia_email_multi_allegati(_attachments(tmp_path), oggetto="Blocked test") is False
    assert mod.flush_email_outbox() == 0
    assert len(attempts) == 1


def test_failed_delivery_remains_pending_and_flush_sends_it_later(tmp_path, monkeypatch):
    mod = _configure_sender(monkeypatch, tmp_path / "outbox")
    monkeypatch.setattr(mod, "EMAIL_SEND_ATTEMPTS", 1)

    class DropSMTP(_FakeSMTP):
        def login(self, *_args):  # before transmission (review G5 R1): safe to retry later
            raise smtplib.SMTPServerDisconnected("drop")

    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", DropSMTP)
    assert mod.invia_email_multi_allegati(_attachments(tmp_path), oggetto="Pending test") is False
    assert list((tmp_path / "outbox").glob("*.job.json"))

    _FakeSMTP.attempts = 0
    _FakeSMTP.timeouts = []
    _FakeSMTP.outcomes = [None]
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _FakeSMTP)

    assert mod.flush_email_outbox() == 1
    assert not list((tmp_path / "outbox").glob("*.job.json"))
    assert not list((tmp_path / "outbox").glob("*.eml"))


def test_worker_starts_and_stops_without_leaking_thread(monkeypatch):
    import bellomberg.reporting.email_sender as mod

    flushed = threading.Event()

    def fake_flush():
        flushed.set()
        return 0

    worker = mod.EmailOutboxWorker(interval_seconds=0.01, flush_fn=fake_flush)
    worker.start()
    assert flushed.wait(1)
    worker.stop()

    assert worker.thread is None


def test_receipt_failure_before_transmission_is_never_flushed_later(tmp_path, monkeypatch):
    mod = _configure_sender(monkeypatch, tmp_path / "outbox")

    class ConnectFailSMTP(_FakeSMTP):
        def __init__(self, *_args, **kwargs):
            raise ConnectionError("synthetic outage before login")

    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", ConnectFailSMTP)
    receipt = {}
    assert not mod.invia_email_multi_allegati(_attachments(tmp_path), oggetto="Receipt test",
                                              delivery_receipt=receipt)
    assert receipt["email_status"] == "failed"
    assert receipt["smtp_state"] == "connecting"

    _FakeSMTP.attempts = 0
    _FakeSMTP.timeouts = []
    _FakeSMTP.outcomes = [None]
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL", _FakeSMTP)
    assert mod.flush_email_outbox() == 0
    assert _FakeSMTP.attempts == 0
    assert receipt["email_status"] == "failed"


def test_email_env_numbers_absent_use_default_and_invalid_are_declared():
    # Maintainer rule: absent = default; empty, non-integer or <= 0 = error naming the variable.
    from bellomberg.reporting.email_sender import _positive_int_value
    assert _positive_int_value(None, 3, "EMAIL_SEND_ATTEMPTS") == 3
    assert _positive_int_value("5", 3, "EMAIL_SEND_ATTEMPTS") == 5
    for bad in ("", "0", "-2", "tre"):
        with pytest.raises(ValueError, match="EMAIL_SEND_ATTEMPTS"):
            _positive_int_value(bad, 3, "EMAIL_SEND_ATTEMPTS")
