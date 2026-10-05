"""
Modulo per inviare il briefing via email Gmail.

Usa smtplib (libreria standard Python) + Gmail SMTP server.
Richiede una Gmail App Password (NON la password normale dell'account).
Vedi SETUP_SCHEDULAZIONE.md per come crearne una.
"""
import base64
import json
import os
import socket
import smtplib
import ssl
import threading
import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.parser import BytesParser
from email.policy import compat32
from datetime import datetime, timedelta, timezone
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from bellomberg.reporting.i18n import label as _t, localized

from bellomberg.core.config import EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO
from bellomberg.core.paths import DATA_DIR


# Gmail SMTP settings (standard, non cambiare)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465  # SSL port
OUTBOX_DIR = Path(DATA_DIR) / "email_outbox"
EMAIL_SMTP_TIMEOUT = 120
EMAIL_SEND_ATTEMPTS = 3
EMAIL_RETRY_DELAY_SECONDS = 15
# A job not delivered within the TTL is never sent late: it is blocked as 'expired'.
EMAIL_OUTBOX_TTL_HOURS = 24
# Blocked/orphan jobs keep a full MIME copy of private memos: removed after retention.
EMAIL_OUTBOX_RETENTION_DAYS = 7
_OUTBOX_LOCK = threading.RLock()
_CONFIG_ERRORS = []


def _positive_int_value(raw, default, name):
    """Absent = default; empty, non-integer or <= 0 = error naming the variable."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise ValueError("%s: valore non valido %r (intero positivo)" % (name, raw))
    return value


def _env_positive_int(name, default):
    """An invalid value is a declared configuration error, never a default in its place.

    It is recorded instead of raised: the API imports this module at start-up and one bad
    email variable must not take the whole backend down. Sending and the outbox worker
    refuse to run while an error is recorded (email_config_error() names the variable).
    """
    try:
        return _positive_int_value(os.getenv(name), default, name)
    except ValueError as error:
        _CONFIG_ERRORS.append(str(error))
        return None


EMAIL_SMTP_TIMEOUT = _env_positive_int("EMAIL_SMTP_TIMEOUT", EMAIL_SMTP_TIMEOUT)
EMAIL_SEND_ATTEMPTS = _env_positive_int("EMAIL_SEND_ATTEMPTS", EMAIL_SEND_ATTEMPTS)
EMAIL_RETRY_DELAY_SECONDS = _env_positive_int("EMAIL_RETRY_DELAY_SECONDS", EMAIL_RETRY_DELAY_SECONDS)
EMAIL_OUTBOX_TTL_HOURS = _env_positive_int("EMAIL_OUTBOX_TTL_HOURS", EMAIL_OUTBOX_TTL_HOURS)
EMAIL_OUTBOX_RETENTION_DAYS = _env_positive_int("EMAIL_OUTBOX_RETENTION_DAYS",
                                                EMAIL_OUTBOX_RETENTION_DAYS)
if (EMAIL_OUTBOX_TTL_HOURS and EMAIL_OUTBOX_RETENTION_DAYS
        and EMAIL_OUTBOX_RETENTION_DAYS * 24 < EMAIL_OUTBOX_TTL_HOURS):
    _CONFIG_ERRORS.append("EMAIL_OUTBOX_RETENTION_DAYS: %d giorni non coprono "
                          "EMAIL_OUTBOX_TTL_HOURS=%d ore" % (EMAIL_OUTBOX_RETENTION_DAYS,
                                                            EMAIL_OUTBOX_TTL_HOURS))
if _CONFIG_ERRORS:
    print("  [!] Email: configurazione non valida, invio e outbox spenti: " + "; ".join(_CONFIG_ERRORS))


def email_config_error():
    """Declared configuration error (variable name included), or None."""
    return "; ".join(_CONFIG_ERRORS) or None


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _job_path(job_id):
    return Path(OUTBOX_DIR) / (job_id + ".job.json")


def _job_id_of(job_path):
    return Path(job_path).name[:-len(".job.json")]


def _sidecar(job_id, kind):
    """Small files next to a job: 'claim' (OS lock), 'attempt' (SMTP phase), 'sent'."""
    return Path(OUTBOX_DIR) / (job_id + "." + kind)


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_utc(value):
    """Job timestamps are UTC ISO strings; a naive legacy value is read as local time."""
    moment = datetime.fromisoformat(str(value))
    return moment.astimezone(timezone.utc)


# --- Cross-process claim -------------------------------------------------------------
# The weekly run is a subprocess, the outbox worker lives in the API process and the
# Trade Idea sends from an API request thread: a lock inside one process protects none of
# that. A job is sent only by whoever holds an OS lock on '<job>.claim'. The OS releases
# it when the holder exits or dies, so a crash never leaves a job owned forever; what the
# dead holder was doing is read from the 'attempt' and 'sent' markers.

def _acquire_claim(job_id):
    """Non-blocking exclusive claim; returns an fd to pass to _release_claim, or None."""
    path = _sidecar(job_id, "claim")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Inside the try (review G5, R4): on Windows a claim file in 'delete pending' state
        # refuses the open with PermissionError -- that is a busy claim, not a broken round.
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # POSIX lets a releasing holder unlink the path: a lock on a stale inode is no claim.
        held, current = os.fstat(fd), os.stat(path)
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise OSError("claim file replaced")
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_claim(fd, job_id, remove=False):
    """Release the claim; with remove=True the claim file goes too (job finished)."""
    path = _sidecar(job_id, "claim")
    try:
        if remove and os.name != "nt":
            path.unlink(missing_ok=True)
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)
    if remove and os.name == "nt":
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            # Another process opened it in the meantime: prune_email_outbox removes it later.
            print("  [!] Outbox: file claim non rimosso (%s): %s" % (path.name, error))


def _write_attempt_marker(job_id, phase):
    """'pre_data' before connecting, 'data' right before the DATA command."""
    _atomic_write_text(_sidecar(job_id, "attempt"),
                       json.dumps({"phase": phase, "pid": os.getpid(),
                                   "at": _utc_now().isoformat(timespec="seconds")}))


def _read_attempt_phase(job_id):
    path = _sidecar(job_id, "attempt")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("phase") or "unknown"
    except (OSError, ValueError):
        return "unknown"  # unreadable = cannot prove DATA never started


def _queue_message(msg, receipt_tracked=False):
    """Persist a complete MIME message before its first network attempt.

    The claim is taken BEFORE the job file exists, so no other process or thread can ever
    see this job unowned; 'receipt_tracked' is written at queue time, so the worker knows
    from the first byte on disk that it must never send this job. Returns (path, claim).
    """
    job_id = "email_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex
    claim = _acquire_claim(job_id)
    if claim is None:
        raise OSError("Outbox: claim non ottenibile per un job nuovo " + job_id)
    created = _utc_now()
    payload = base64.b64encode(msg.as_bytes()).decode("ascii")
    job = {
        "job_id": job_id,
        "created_at": created.isoformat(timespec="seconds"),
        "expires_at": (created + timedelta(hours=EMAIL_OUTBOX_TTL_HOURS)).isoformat(timespec="seconds"),
        "receipt_tracked": bool(receipt_tracked),
        "owner_pid": os.getpid(),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "subject": msg.get("Subject", ""),
        "message_b64": payload,
        "attempts": 0,
        "last_error": None,
        "blocked": False,
    }
    try:
        with _OUTBOX_LOCK:
            _atomic_write_text(_job_path(job_id), json.dumps(job, ensure_ascii=False))
    except BaseException:
        _release_claim(claim, job_id, remove=True)
        raise
    return _job_path(job_id), claim


def _retryable_smtp_error(error):
    if isinstance(error, (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused,
                          smtplib.SMTPSenderRefused)):
        return False
    if isinstance(error, smtplib.SMTPDataError):
        return 400 <= int(getattr(error, "smtp_code", 0)) < 500
    return isinstance(error, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected,
                               TimeoutError, socket.timeout, ConnectionError, OSError))


def _watch_smtp_phases(server, phase, on_data=None):
    """Observe MAIL and DATA on the real smtplib client (send_message -> sendmail).

    Instance attributes shadow the class methods that sendmail calls through self.
    on_data runs BEFORE the DATA command is written: if it fails, nothing was transmitted.
    """
    mail = getattr(server, "mail", None)
    data = getattr(server, "data", None)
    # Both observed: an error with neither MAIL nor DATA seen happened before MAIL
    # (EHLO, SMTPUTF8 check...). A client lacking them proves nothing (phase unknown).
    phase["watched"] = callable(mail) and callable(data)
    if callable(mail):
        def _mail(*args, **kwargs):
            phase["envelope"] = True
            return mail(*args, **kwargs)
        server.mail = _mail
    if callable(data):
        def _data(*args, **kwargs):
            if on_data is not None:
                on_data()
            phase["data"] = True
            return data(*args, **kwargs)
        server.data = _data


def _rejected_before_data(error, phase):
    """True only when it is PROVEN that the DATA command never started.

    MAIL FROM / RCPT refusals are raised by sendmail before data(); any other error
    counts as pre-DATA only on a client whose MAIL and DATA are both observed and DATA was
    not reached -- between login and MAIL included (review G5, R6). A client that bypasses
    the observed methods proves nothing: the outcome stays unknowable.
    """
    if phase.get("data"):
        return False
    if isinstance(error, (smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused)):
        return True
    return bool(phase.get("envelope") or phase.get("watched"))


def _explicit_data_rejection_code(error):
    """4xx/5xx code of a readable server rejection of DATA, else None.

    smtplib raises SMTPDataError only from a parsed reply (DATA command not answered 354,
    or end of content not answered 250). An unparsable reply arrives with code -1 and stays
    'uncertain', like a dropped connection.
    """
    if not isinstance(error, smtplib.SMTPDataError):
        return None
    try:
        code = int(getattr(error, "smtp_code", 0))
    except (TypeError, ValueError):
        return None
    return code if 400 <= code < 600 else None


def _send_message_with_retry(msg, delivery_receipt=None, on_attempt=None, on_data=None):
    """Send one MIME message, reconnecting for transient SMTP failures.

    Returns (sent, last_error, ambiguous). The SMTP state is tracked per attempt:
    a lost QUIT after an observed successful DATA response is still a delivery.

    PM decision (04/10/2026) when the caller tracks a delivery receipt: only errors that
    happen BEFORE transmission may be retried -- connection, TLS, authentication, MAIL
    FROM/RCPT refused (the server accepted nothing). From the start of DATA onwards nothing
    is retried automatically (at-most-once):
    - an explicit, readable 4xx/5xx rejection of the DATA command or of the end of the
      content is 'failed' (the message was not delivered), declared with code and kind
      (4xx temporary, 5xx permanent) -- PM decision of 04/10 evening;
    - a lost connection, a timeout or an unreadable reply after DATA started is 'uncertain'.
    Without a receipt (review G5, R1) the same line holds for ambiguity: after DATA started
    a lost connection, a timeout or an unreadable reply is ambiguous and never retried, in
    line or by the worker (the job is blocked 'uncertain'); only errors before transmission
    and explicit 4xx rejections are retried, within the bounded attempts and the TTL.
    """
    last_error = None
    attempts_made = 0
    smtp_state = "not_started"
    for attempt in range(1, EMAIL_SEND_ATTEMPTS + 1):
        attempts_made = attempt
        smtp_state = "connecting"
        phase = {"envelope": False, "data": False}
        try:
            if on_attempt is not None:
                on_attempt()
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context,
                                  timeout=EMAIL_SMTP_TIMEOUT) as server:
                server.login(EMAIL_FROM, EMAIL_PASSWORD)
                smtp_state = "sending"
                if delivery_receipt is not None:
                    delivery_receipt["smtp_state"] = smtp_state
                _watch_smtp_phases(server, phase, on_data)
                refused = server.send_message(msg)
                if refused:
                    if delivery_receipt is not None:
                        delivery_receipt.update(email_status="uncertain",
                                                email_error="Some recipients were refused")
                    error = smtplib.SMTPRecipientsRefused(refused)
                    print("  [!] SMTP: alcuni destinatari rifiutati: %s" % (refused,))
                    return False, error, True
                smtp_state = "accepted"
                if delivery_receipt is not None:
                    delivery_receipt.update(smtp_state=smtp_state, email_status="accepted",
                                            email_error=None)
            return True, None, False
        except Exception as error:
            # A lost QUIT response cannot revoke an observed successful DATA response.
            if smtp_state == "accepted":
                return True, None, False
            last_error = error
            if smtp_state == "sending" and _rejected_before_data(error, phase):
                smtp_state = "envelope"
                if delivery_receipt is not None:
                    delivery_receipt["smtp_state"] = smtp_state
            code = _explicit_data_rejection_code(error) if smtp_state == "sending" else None
            if delivery_receipt is not None and code is not None:
                kind = "temporaneo" if code < 500 else "permanente"
                delivery_receipt.update(
                    email_status="failed", smtp_state="data_rejected",
                    email_error="SMTPDataError %d: rifiuto esplicito del server al DATA (%s), "
                                "messaggio non consegnato, nessun nuovo tentativo automatico"
                                % (code, kind))
                print("  [!] SMTP: DATA rifiutato dal server (%d, %s), nessun nuovo tentativo: %s"
                      % (code, kind, error))
                return False, error, False
            if smtp_state == "sending" and code is None:
                if delivery_receipt is not None:
                    delivery_receipt.update(email_status="uncertain", smtp_state=smtp_state,
                                            email_error=type(error).__name__)
                print("  [!] SMTP error durante l'invio (esito incerto, nessun nuovo tentativo): %s"
                      % error)
                return False, error, True
            if (not _retryable_smtp_error(error)
                    or attempt >= EMAIL_SEND_ATTEMPTS):
                break
            delay = EMAIL_RETRY_DELAY_SECONDS * attempt
            print("  [!] SMTP tentativo %d/%d fallito (%s): nuovo tentativo tra %ds" %
                  (attempt, EMAIL_SEND_ATTEMPTS, type(error).__name__, delay))
            time.sleep(delay)
    if delivery_receipt is not None:
        delivery_receipt.update(email_status="failed", smtp_state=smtp_state,
                                email_error=type(last_error).__name__)
    print("  [!] SMTP error dopo %d tentativo/i: %s" % (attempts_made, last_error))
    return False, last_error, False


def _block_job(job_path, job, reason):
    """Persist a block on a job this caller has claimed; the reason is declared."""
    job["blocked"] = True
    job["blocked_reason"] = reason
    job["blocked_at"] = _utc_now().isoformat(timespec="seconds")
    print("  [!] Outbox: job %s bloccato, non verra' inviato: %s" % (job_path.name, reason))
    try:
        with _OUTBOX_LOCK:
            _atomic_write_text(job_path, json.dumps(job, ensure_ascii=False))
    except OSError as error:
        print("  [!] Outbox: blocco non persistito per %s: %s" % (job_path.name, error))


def _finish_sent_job(job_id, delivery_receipt=None):
    """Remove a delivered job. A failed cleanup is declared, never turned into 'not sent'.

    The markers that prove the delivery ('sent', 'attempt' = accepted) are removed only
    after the job file is gone (review G5, R3): a surviving job is never left unmarked.
    """
    problems = []
    for path in (_job_path(job_id), _sidecar(job_id, "attempt"), _sidecar(job_id, "sent")):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            problems.append("%s: %s: %s" % (path.name, type(error).__name__, error))
            break
    if problems:
        message = "; ".join(problems)
        print("  [!] Outbox: email INVIATA ma pulizia del job fallita (nessun nuovo invio): " + message)
        if delivery_receipt is not None:
            delivery_receipt["outbox_cleanup_error"] = message
    return problems


def _deliver_claimed(job_path, job, delivery_receipt=None):
    """Send a job whose claim the caller holds. Returns True only for an SMTP acceptance."""
    job_id = _job_id_of(job_path)
    msg = BytesParser(policy=compat32).parsebytes(
        base64.b64decode(job["message_b64"].encode("ascii")))

    def mark_data():
        _write_attempt_marker(job_id, "data")

    sent, error, ambiguous = _send_message_with_retry(
        msg, delivery_receipt=delivery_receipt,
        on_attempt=lambda: _write_attempt_marker(job_id, "pre_data"), on_data=mark_data)
    if sent:
        # Two independent proofs of delivery, so that one failed write is not enough to
        # resend (review G5, R3): the attempt marker goes to 'accepted', then the 'sent'
        # marker, a new small file that can be created even while a reader holds the job.
        try:
            _write_attempt_marker(job_id, "accepted")
        except OSError as marker_error:
            print("  [!] Outbox: marcatore 'accepted' non scritto per %s: %s" % (job_id, marker_error))
        try:
            _sidecar(job_id, "sent").write_text(_utc_now().isoformat(timespec="seconds"),
                                                encoding="utf-8")
        except OSError as marker_error:
            print("  [!] Outbox: marcatore 'sent' non scritto per %s: %s" % (job_id, marker_error))
        _finish_sent_job(job_id, delivery_receipt)
        return True
    with _OUTBOX_LOCK:
        try:
            job["attempts"] = int(job.get("attempts", 0)) + 1
            job["last_error"] = "%s: %s" % (type(error).__name__, error)
            # An ambiguous transmission is never re-sent automatically by the worker.
            job["blocked"] = bool(error and (ambiguous or not _retryable_smtp_error(error)))
            if job["blocked"]:
                job["blocked_reason"] = ("uncertain: " if ambiguous else "failed: ") + job["last_error"]
            if delivery_receipt is not None:
                # The caller's receipt is the single source of truth: the worker must
                # never deliver later what the receipt already records as not sent.
                job["blocked"] = True
                job["receipt_outcome"] = delivery_receipt.get("email_status")
                job.setdefault("blocked_reason", "receipt: " + str(job["receipt_outcome"]))
            _atomic_write_text(job_path, json.dumps(job, ensure_ascii=False))
            if not ambiguous:
                _sidecar(job_id, "attempt").unlink(missing_ok=True)
        except OSError as state_error:
            # Not sent: a stale 'attempt' marker can only make a later claimant more careful.
            print("  [!] Outbox state update failed: %s" % state_error)
    return False


def _deliver_job(job_path):
    """Worker path: claim, inspect what earlier holders left, send only if allowed."""
    job_path = Path(job_path)
    job_id = _job_id_of(job_path)
    claim = _acquire_claim(job_id)
    if claim is None:
        return False  # owned by a live sender (run, API request or another worker)
    finished = False
    try:
        if _sidecar(job_id, "sent").exists() or _read_attempt_phase(job_id) == "accepted":
            # Delivered by an earlier holder whose cleanup failed or was interrupted.
            finished = not _finish_sent_job(job_id)
            return False
        if not job_path.exists():
            finished = True
            return False
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception as error:
            print("  [!] Outbox job illeggibile: %s (%s)" % (job_path.name, error))
            return False
        if job.get("blocked"):
            return False
        if "receipt_tracked" not in job:
            _block_job(job_path, job, "legacy: job senza marcatura della ricevuta "
                                      "(scritto prima del claim), mai reinviato")
            return False
        if job["receipt_tracked"]:
            # Its sender owns the outcome through the receipt: never sent by the worker.
            return False
        phase = _read_attempt_phase(job_id)
        if phase is not None and phase != "pre_data":
            _block_job(job_path, job, "uncertain: il processo che inviava e' terminato "
                                      "durante il DATA (fase %s)" % phase)
            return False
        try:
            expired = _utc_now() >= _parse_utc(job["expires_at"])
        except (KeyError, TypeError, ValueError):
            expired = True
        if expired:
            _block_job(job_path, job, "expired: job scaduto il %s senza consegna"
                                      % job.get("expires_at"))
            return False
        sent = _deliver_claimed(job_path, job)
        finished = sent
        return sent
    finally:
        _release_claim(claim, job_id, remove=finished)


def flush_email_outbox():
    """Attempt all pending jobs once; return how many were delivered."""
    if email_config_error():
        print("  [!] Outbox non svuotato: " + email_config_error())
        return 0
    outbox = Path(OUTBOX_DIR)
    if not outbox.exists():
        return 0
    delivered = 0
    for job_path in sorted(outbox.glob("*.job.json")):
        if _deliver_job(job_path):
            delivered += 1
    return delivered


def _job_created_from_id(job_id):
    """Creation time from the job id ('email_YYYYmmdd_HHMMSS_<hex>', local time).

    Prune never opens a job file (review G5, R5): reading a multi-MB MIME every tick kept it
    open and made the sender's replace/unlink fail on Windows. A non-standard id falls back
    to the file mtime (a stat, no open for reading); None if neither is available.
    """
    try:
        stamp = job_id.split("_")[1] + job_id.split("_")[2]
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").astimezone(timezone.utc)
    except (IndexError, ValueError):
        try:
            return datetime.fromtimestamp(_job_path(job_id).stat().st_mtime, timezone.utc)
        except OSError:
            return None


def prune_email_outbox(now=None):
    """Remove jobs older than the retention (blocked, expired or orphaned) and stray files.

    Every removal and every failure is declared. Jobs owned by a live sender are skipped.
    """
    result = {"removed": [], "errors": []}
    if email_config_error():
        result["errors"].append(email_config_error())
        return result
    outbox = Path(OUTBOX_DIR)
    if not outbox.exists():
        return result
    now = now or _utc_now()
    limit = now - timedelta(days=EMAIL_OUTBOX_RETENTION_DAYS)
    job_ids = {_job_id_of(path) for path in outbox.glob("*.job.json")}
    for job_id in sorted(job_ids):
        old = _job_created_from_id(job_id)
        if old is None:
            continue
        old = old < limit
        if not old:
            continue
        claim = _acquire_claim(job_id)
        if claim is None:
            continue
        removed = True
        try:
            for path in (_job_path(job_id), _sidecar(job_id, "attempt"), _sidecar(job_id, "sent")):
                try:
                    path.unlink(missing_ok=True)
                except OSError as error:
                    removed = False
                    result["errors"].append("%s: %s" % (path.name, error))
        finally:
            _release_claim(claim, job_id, remove=removed)
        if removed:
            result["removed"].append(job_id)
    # Sidecars whose job is gone (cleanup interrupted after the job was removed).
    for path in sorted(outbox.iterdir()):
        name = path.name
        for kind in ("claim", "attempt", "sent"):
            suffix = "." + kind
            if name.endswith(suffix) and name[:-len(suffix)] not in job_ids:
                job_id = name[:-len(suffix)]
                # Claim first, then re-check (review G5, R2): a job queued after the listing
                # above owns live markers that must never be touched.
                claim = _acquire_claim(job_id)
                if claim is None:
                    continue
                gone = not _job_path(job_id).exists()
                try:
                    if gone and kind != "claim":
                        path.unlink(missing_ok=True)
                except OSError as error:
                    result["errors"].append("%s: %s" % (name, error))
                finally:
                    _release_claim(claim, job_id, remove=gone)
    if result["removed"]:
        print("  [Outbox] job rimossi dopo %d giorni di conservazione: %s"
              % (EMAIL_OUTBOX_RETENTION_DAYS, ", ".join(result["removed"])))
    if result["errors"]:
        print("  [!] Outbox: pulizia incompleta: " + "; ".join(result["errors"]))
    return result


class EmailOutboxWorker:
    """Drain pending email jobs while the API process remains alive."""

    def __init__(self, interval_seconds=60, flush_fn=None, prune_fn=None):
        self.interval_seconds = max(1, float(interval_seconds))
        self.flush_fn = flush_fn or flush_email_outbox
        self.prune_fn = prune_fn or prune_email_outbox
        self.stop_event = threading.Event()
        self.thread = None
        self.status = {"running": False, "reason": "not started"}

    def start(self):
        """Start the worker, or declare why it stays off. Returns the status dict."""
        if self.thread is not None and self.thread.is_alive():
            return self.status
        error = email_config_error()
        if error:
            self.status = {"running": False, "reason": "configurazione email non valida: " + error}
            print("  [!] Email outbox worker SPENTO: " + self.status["reason"])
            return self.status
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop,
                                       name="bellomberg-email-outbox",
                                       daemon=True)
        self.thread.start()
        self.status = {"running": True, "reason": None}
        return self.status

    def stop(self):
        thread = self.thread
        self.stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        if thread is None or not thread.is_alive():
            self.thread = None
            self.status = {"running": False, "reason": "stopped"}

    def _loop(self):
        while not self.stop_event.is_set():
            for step in (self.flush_fn, self.prune_fn):
                if self.stop_event.is_set():
                    break
                try:
                    step()
                except Exception as error:
                    print("  [!] Email outbox worker error: %s" % error)
            self.stop_event.wait(self.interval_seconds)


def _queue_and_deliver(msg, delivery_receipt=None):
    error = email_config_error()
    if error:
        print("  [!] Email NON inviata: " + error)
        if delivery_receipt is not None:
            delivery_receipt.update(email_status="failed", smtp_state="not_started",
                                    email_error="Configurazione email non valida: " + error)
        return False
    job_path, claim = _queue_message(msg, receipt_tracked=delivery_receipt is not None)
    job_id = _job_id_of(job_path)
    sent = False
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        sent = _deliver_claimed(job_path, job, delivery_receipt=delivery_receipt)
    finally:
        _release_claim(claim, job_id, remove=sent)
    if sent:
        print("  [OK] Email inviata a " + EMAIL_TO)
    elif delivery_receipt is None:
        print("  [!] Email accodata per nuovo tentativo: " + job_path.name)
    else:
        print("  [!] Email non inviata (%s): job conservato e bloccato %s"
              % (delivery_receipt.get("email_status"), job_path.name))
    return sent


def email_configurata():
    """Verifica se le credenziali email sono nel .env."""
    return bool(EMAIL_FROM and EMAIL_PASSWORD and EMAIL_TO)


def _markdown_to_html(testo_md):
    """
    Conversione semplice markdown -> HTML per email.
    Non e' un parser completo, gestisce solo i pattern che usiamo nel briefing:
    # heading, ## subheading, **bold**, paragrafi.
    """
    righe = testo_md.split("\n")
    html_parti = []
    in_paragrafo = False

    for riga in righe:
        riga = riga.rstrip()
        if not riga:
            if in_paragrafo:
                html_parti.append("</p>")
                in_paragrafo = False
            continue

        # Heading
        if riga.startswith("# "):
            if in_paragrafo:
                html_parti.append("</p>")
                in_paragrafo = False
            html_parti.append("<h1>" + riga[2:] + "</h1>")
        elif riga.startswith("## "):
            if in_paragrafo:
                html_parti.append("</p>")
                in_paragrafo = False
            html_parti.append("<h2>" + riga[3:] + "</h2>")
        elif riga.startswith("### "):
            if in_paragrafo:
                html_parti.append("</p>")
                in_paragrafo = False
            html_parti.append("<h3>" + riga[4:] + "</h3>")
        else:
            # Trasforma **testo** in <strong>
            import re
            riga = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', riga)
            if not in_paragrafo:
                html_parti.append("<p>")
                in_paragrafo = True
            html_parti.append(riga + " ")

    if in_paragrafo:
        html_parti.append("</p>")

    # Wrap con stile email-friendly
    css = """
    <style>
        body { font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
               max-width: 720px; margin: 20px auto; padding: 20px; color: #1a1a1a;
               line-height: 1.6; }
        h1 { color: #0a3d62; border-bottom: 2px solid #0a3d62; padding-bottom: 8px; }
        h2 { color: #0a3d62; margin-top: 28px; border-bottom: 1px solid #ccc;
             padding-bottom: 4px; }
        h3 { color: #444; margin-top: 20px; }
        p { margin: 12px 0; }
        strong { color: #0a3d62; }
    </style>
    """
    body = "\n".join(html_parti)
    return "<html><head>" + css + "</head><body>" + body + "</body></html>"


@localized
def invia_briefing_email(testo_briefing, oggetto=None):
    """
    Invia il briefing markdown come email HTML al destinatario configurato.
    Restituisce True se OK, False se errore.
    """
    if not email_configurata():
        print("  [!] Credenziali email non configurate, skip invio.")
        print("      Configura EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO nel .env")
        return False

    if not oggetto:
        oggetto = _t("Daily Portfolio Briefing - ") + datetime.now().strftime("%d/%m/%Y")

    # Costruisci messaggio multipart (HTML + fallback plain text)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Parte plain text (fallback per client che non leggono HTML)
    parte_text = MIMEText(testo_briefing, "plain", "utf-8")
    parte_html = MIMEText(_markdown_to_html(testo_briefing), "html", "utf-8")
    msg.attach(parte_text)
    msg.attach(parte_html)

    return _queue_and_deliver(msg)


@localized
def invia_briefing_email_con_allegato(testo_briefing, oggetto=None, allegato_path=None):
    """Come invia_briefing_email ma con file allegato (PDF report)."""
    import os as _os
    from email.mime.base import MIMEBase
    from email import encoders

    if not email_configurata():
        print("  [!] Credenziali email non configurate, skip invio.")
        return False

    if not oggetto:
        oggetto = _t("Daily Portfolio Briefing - ") + datetime.now().strftime("%d/%m/%Y")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Corpo (alternative: plain + html)
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(testo_briefing, "plain", "utf-8"))
    body.attach(MIMEText(_markdown_to_html(testo_briefing), "html", "utf-8"))
    msg.attach(body)

    # Allegato PDF
    attach_ok = False
    if allegato_path and _os.path.exists(allegato_path):
        try:
            with open(allegato_path, "rb") as f:
                part = MIMEBase("application", "pdf")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            filename = _os.path.basename(allegato_path)
            part.add_header("Content-Disposition", "attachment; filename=" + filename)
            msg.attach(part)
            attach_ok = True
            print("  [Allegato] " + filename + " (" + str(_os.path.getsize(allegato_path)) + " bytes)")
        except Exception as e:
            print("  [!] Errore allegando PDF: " + str(e))
    elif allegato_path:
        print("  [!] Allegato non trovato: " + str(allegato_path))
    if allegato_path and not attach_ok:
        # audit/11 §5: prima l'email partiva SENZA report e la run risultava consegnata
        # — ora il PM lo vede dall'oggetto
        try:
            msg.replace_header("Subject", (msg["Subject"] or "") + _t(" [ALLEGATO MANCANTE]"))
        except Exception:
            pass

    return _queue_and_deliver(msg)


@localized
def invia_pdf_only_email(pdf_path, oggetto=None):
    """Invia email con SOLO il PDF allegato. Corpo minimale, niente markdown."""
    import os as _os
    from email.mime.base import MIMEBase
    from email import encoders

    if not email_configurata():
        return False
    if not pdf_path or not _os.path.exists(pdf_path):
        print("  [!] PDF non esiste: " + str(pdf_path))
        return False

    if not oggetto:
        oggetto = _t("Weekly Research Note - ") + datetime.now().strftime("%d/%m/%Y")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    # Corpo minimale (1 riga, in caso il client mostri qualcosa)
    body_html = '<html><body><p>' + _t("email.pdf_attached") + '</p><p>' + _t("email.committee") + '</p></body></html>'
    msg.attach(MIMEText(_t("email.pdf_attached"), "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    # PDF allegato
    try:
        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "pdf")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        filename = _os.path.basename(pdf_path)
        part.add_header("Content-Disposition", "attachment; filename=" + filename)
        msg.attach(part)
    except Exception as e:
        print("  [!] PDF attach error: " + str(e))
        return False

    return _queue_and_deliver(msg)


_CHIAVI_FV = ("fair_value_final", "fair_value_weighted", "fair_value_blend",
              "fair_value_base", "fair_value_nav")


@localized
def corpo_valutazioni(valuation_results, allegati, delivery=None):
    """Blocco HTML per il corpo dell'email: l'esito di OGNI valutazione chiesta dal comitato
    e i modelli Excel davvero allegati.

    Audit run 10/09 (Fable 5.1, memo #54): era stato chiesto un fair value, il desk ha
    chiamato get_valuation, il motore ha risposto FV n.d. (11 campi documentati mancanti) e
    l'email e' partita con due PDF e un piede fisso che prometteva "DCF Excel models for
    GREEN-validated candidates". Chi legge l'email non poteva distinguere un modello mai
    generato da uno perso per strada. Qui si dichiara: niente Excel = detto; FV n.d. = detto
    col motivo e i campi mancanti; nessuna valutazione chiesta = detto."""
    import os as _os
    import html as _html
    xlsx = [p for p in (allegati or []) if str(p).lower().endswith((".xlsx", ".xls"))]
    righe = []
    if xlsx:
        righe.append(_t("email.models", count=len(xlsx),
                        files=", ".join(_html.escape(_os.path.basename(str(p))) for p in xlsx)))
    else:
        righe.append(_t("<p><b>Nessun modello Excel allegato.</b></p>"))
    if not valuation_results:
        righe.append(_t("<p>Nessuna valutazione (get_valuation) richiesta dai desk in questa run.</p>"))
        return "\n".join(righe)
    righe.append(_t("<p><b>Valutazioni richieste dal comitato:</b></p>\n<ul>"))
    for tk, res in sorted((valuation_results or {}).items()):
        res = res if isinstance(res, dict) else {}
        us = res.get("valuation_usability") if isinstance(res.get("valuation_usability"), dict) else {}
        fv = next((res[k] for k in _CHIAVI_FV if res.get(k) is not None), None)
        if us.get("usable") and fv is not None:
            testo = "FV %s %s" % (fv, res.get("currency") or "")
        else:
            sanity = res.get("sanity") if isinstance(res.get("sanity"), dict) else {}
            motivo = sanity.get("headline") or res.get("error") or _t("motivo n.d.")
            mancanti = [m for m in (us.get("missing_fields") or []) if m != "fair_value"]
            testo = _t("FV n.d. - %s") % str(motivo)[:160]
            if mancanti:
                testo += _t("; campi mancanti (%d): %s") % (
                    len(mancanti), ", ".join(str(m) for m in mancanti[:12]))
        snap = res.get("snapshot_id")
        from bellomberg.core.language import text as translated
        publication = res.get("model_publication") or {}
        testo += translated("; pubblicazione modello: ", "; model publication: ") + str(publication.get("status", "not_verified"))
        if publication.get("reason"):
            testo += " (" + str(publication["reason"]) + ")"
        testo += translated("; allegato = copia datata, senza aggiornamento remoto",
                            "; attachment = dated copy, without remote updates")
        if res.get("reused"):
            testo += translated("; revisione esistente riutilizzata, nessuna nuova analisi",
                                "; existing revision reused, no new analysis")
        if delivery is not None:
            outcome = next((row for row in delivery.get("valuations", []) if row["ticker"] == tk), None)
            if outcome:
                testo += "; Excel: " + outcome["status"] + " (" + outcome["reason"] + ")"
        from bellomberg.reporting.valuation_quote import quote_comparison_text
        testo += "; " + quote_comparison_text(res)
        righe.append("<li><b>%s</b>: %s%s</li>" % (
            _html.escape(str(tk)), _html.escape(testo.strip()),
            (" [snapshot %s]" % _html.escape(str(snap)[:12])) if snap else ""))
    righe.append("</ul>")
    return "\n".join(righe)


@localized
def corpo_azioni(assessments):
    """HTML summary from the publication snapshot embedded in the memo PDF."""
    import html as _html
    righe = ["<p><b>Esito operativo delle righe ACTION TABLE:</b></p>",
             "<ul>"]
    if not assessments:
        righe.append("<li>Nessuna riga ACTION TABLE verificata.</li>")
    for item in assessments or []:
        if not isinstance(item, dict):
            continue
        action = _html.escape(str(item.get("action") or ""))
        ticker = _html.escape(str(item.get("ticker") or ""))
        status = _html.escape(str(item.get("status") or "CHECK_UNAVAILABLE"))
        eur = item.get("eur")
        amount = (" — EUR " + _html.escape(str(eur))) if eur is not None else ""
        reason = _html.escape(str(item.get("reason") or "Motivo non disponibile."))
        rationale = str(item.get("override_rationale") or "").strip()
        extra = ("; deroga dichiarata: " + _html.escape(rationale)) if rationale else ""
        righe.append("<li><b>%s %s%s — %s</b>: %s%s</li>" % (
            action, ticker, amount, status, reason, extra))
    righe.append("</ul>")
    return "\n".join(righe)


@localized
def invia_email_multi_allegati(pdf_paths, oggetto=None, body_extra="", *,
                             expected_hashes=None, delivery_receipt=None,
                             message_title=None, message_id=None, require_all_hashes=False):
    """Invia email con N allegati (PDF memo + PDF appendice + N Excel DCF).

    pdf_paths: list of file paths (PDF + XLSX mixed). I file vengono inferiti via estensione.
    body_extra: HTML gia' pronto (es. corpo_valutazioni) inserito nel corpo.
    """
    import os as _os
    import re as _re
    import html as _html
    from email.mime.base import MIMEBase
    from email import encoders

    if not email_configurata():
        if delivery_receipt is not None:
            delivery_receipt.update(email_status="failed", smtp_state="not_started",
                                    email_error="Email configuration missing")
        return False
    pdf_paths = list(pdf_paths)
    missing = [p for p in pdf_paths if not p or not _os.path.isfile(p)]
    if missing:
        print("  [!] Email NON inviata: allegati richiesti assenti: " + str(missing))
        if delivery_receipt is not None:
            delivery_receipt.update(email_status="package_failed", email_error="Allegati richiesti assenti")
        return False
    if not pdf_paths:
        print("  [!] Nessun allegato valido")
        return False

    if not oggetto:
        oggetto = _t("Weekly Research Note - ") + datetime.now().strftime("%d/%m/%Y")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    if message_id:
        msg["Message-ID"] = message_id

    # Lista allegati nel body
    n_files = len(pdf_paths)
    n_xlsx = sum(1 for p in pdf_paths if str(p).lower().endswith((".xlsx", ".xls")))
    files_list_html = "<ul>" + "".join(
        "<li><b>" + _os.path.basename(p) + "</b></li>" for p in pdf_paths
    ) + "</ul>"
    # Audit 11/09 (Fable 5.1): il piede nominava un modello cablato ("Capo on Opus 4.8",
    # superato dal .env dal 05/09) e prometteva modelli Excel anche con zero .xlsx allegati.
    piede = _t("email.committee") + "<br>"
    if n_xlsx:
        piede += _t("email.excel_footer", count=n_xlsx)
    else:
        piede += _t("email.no_excel_footer")
    body_html = """<html><body style="font-family: -apple-system, Segoe UI, sans-serif; color: #333; max-width: 700px;">
<h2 style="color: #1a1a2e;">{title}</h2>
<p>{attached}:</p>
{files_list}
{extra}
<hr>
<p style="color:#888;font-size:12px;">{piede}</p>
</body></html>""".format(
        title=__import__("html").escape(message_title) if message_title else _t("Weekly Research Note"),
        attached=_t("email.attached_count", count=n_files),
        files_list=files_list_html,
        extra=body_extra or "",
        piede=piede,
    )
    testo_extra = _re.sub(r"<[^>]+>", " ", body_extra or "")
    testo_extra = _html.unescape(testo_extra)
    testo_extra = _re.sub(r"[ \t]+", " ", testo_extra).strip()
    body = MIMEMultipart("alternative")
    body.attach(MIMEText((message_title + " - " if message_title else _t("Weekly Research Note - "))
                         + _t("email.attached_count", count=n_files) + "."
                         + ("\n\n" + testo_extra if testo_extra else ""), "plain", "utf-8"))
    body.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(body)

    # Allegati
    for path in pdf_paths:
        try:
            ext = _os.path.splitext(path)[1].lower()
            if ext == ".pdf":
                part = MIMEBase("application", "pdf")
            elif ext in (".xlsx", ".xls"):
                part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                part = MIMEBase("application", "octet-stream")
            with open(path, "rb") as f:
                contents = f.read()
            if require_all_hashes and (not expected_hashes or str(path) not in expected_hashes):
                raise ValueError("Attachment hash missing from validated manifest")
            if expected_hashes and str(path) in expected_hashes:
                from hashlib import sha256
                if sha256(contents).hexdigest() != expected_hashes[str(path)]:
                    raise ValueError("Allegato modificato dopo la verifica della generazione")
            part.set_payload(contents)
            encoders.encode_base64(part)
            filename = _os.path.basename(path)
            part.add_header("Content-Disposition", "attachment; filename=" + filename)
            msg.attach(part)
            print("  [Attach] " + filename + " (" + str(_os.path.getsize(path)) + " bytes)")
        except Exception as e:
            print("  [!] Attach fail " + path + ": " + str(e))
            print("  [!] Email NON inviata: il pacchetto allegati sarebbe incompleto")
            if delivery_receipt is not None:
                delivery_receipt.update(email_status="package_failed", email_error=type(e).__name__ + ": " + str(e))
            return False

    if delivery_receipt is not None:
        delivery_receipt.update(email_status="package_built", smtp_state="not_started",
                                mime_attachments=[str(p) for p in pdf_paths])
    sent = _queue_and_deliver(msg, delivery_receipt=delivery_receipt)
    if sent:
        print("  [OK] Email con " + str(n_files) + " allegati inviata a " + EMAIL_TO)
    return sent
