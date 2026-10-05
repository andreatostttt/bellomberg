"""Fixture `smtp` condivisa dai test Trade Idea: SMTP finto che cattura il MIME, rete vietata.

Stava in tests/test_trade_idea_delivery.py; spostata qui (05/10/2026, Opus 5.5) perche' quel file
usa il workbook storico privato e resta fuori dal repo pubblico, mentre nove test pubblici
importavano da li' solo questa fixture. Corpo invariato; destinatari e password sintetici.
"""
from email import policy
from email.parser import BytesParser
import socket

import pytest

from bellomberg.reporting import email_sender


@pytest.fixture
def smtp(monkeypatch):
    messages = []

    class CaptureSMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def login(self, *args): pass
        def send_message(self, message):
            messages.append(BytesParser(policy=policy.default).parsebytes(message.as_bytes()))
            return {}

    monkeypatch.setattr(email_sender, "EMAIL_FROM", "research@example.invalid")
    monkeypatch.setattr(email_sender, "EMAIL_TO", "pm@example.invalid")
    monkeypatch.setattr(email_sender, "EMAIL_PASSWORD", "synthetic-password")
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", CaptureSMTP)
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Unexpected real network"))
    return messages, CaptureSMTP
