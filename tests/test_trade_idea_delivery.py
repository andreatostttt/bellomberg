"""Trade Idea MIME and report proof. All issuers, recipients and providers are synthetic."""
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path
import smtplib
import socket
import json

import pytest


def test_provider_schema_requires_all_nested_fields_without_reader_defaults():
    from bellomberg.core.trade_idea_contract import TRADE_IDEA_RESULT_SCHEMA, TradeIdeaResult
    def visit(node):
        if isinstance(node, dict):
            assert "default" not in node
            if node.get("type") == "object" and "properties" in node:
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(TRADE_IDEA_RESULT_SCHEMA)
    assert "history_review" not in TradeIdeaResult.model_json_schema()["required"]

from bellomberg.reporting import email_sender
from test_documented_dcf_contract import contract_tools, _call
from test_valuation_snapshot_persistence import db


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


def test_sender_trade_idea_title_id_and_hash_are_in_the_actual_mime(tmp_path, smtp):
    messages, _ = smtp
    pdf = tmp_path / "research.pdf"
    pdf.write_bytes(b"%PDF synthetic sender-contract fixture")
    receipt = {}
    assert email_sender.invia_email_multi_allegati([str(pdf)], oggetto="Trade Idea SYNTH-EXT rejected",
        message_title="Trade Idea", message_id="<run-fixture@bellomberg.local>",
        expected_hashes={str(pdf): sha256(pdf.read_bytes()).hexdigest()},
        require_all_hashes=True, delivery_receipt=receipt)
    message = messages[0]
    assert message["Message-ID"] == "<run-fixture@bellomberg.local>"
    assert message["To"] == "pm@example.invalid"
    assert "Weekly Research" not in str(message.get_body(preferencelist=("plain",)))
    assert next(message.iter_attachments()).get_payload(decode=True) == pdf.read_bytes()
    assert receipt["smtp_state"] == "accepted"


def test_sender_trade_idea_missing_pdf_hash_never_opens_smtp(tmp_path, smtp):
    messages, _ = smtp
    pdf = tmp_path / "research.pdf"
    pdf.write_bytes(b"synthetic")
    receipt = {}
    assert not email_sender.invia_email_multi_allegati([str(pdf)], expected_hashes={},
        require_all_hashes=True, delivery_receipt=receipt)
    assert receipt["email_status"] == "package_failed"
    assert messages == []


def test_sender_reports_ambiguous_transport_without_claiming_success(tmp_path, smtp, monkeypatch):
    _, capture = smtp
    pdf = tmp_path / "research.pdf"
    pdf.write_bytes(b"synthetic")
    def uncertain(*args):
        raise smtplib.SMTPServerDisconnected("Synthetic disconnect after DATA")
    monkeypatch.setattr(capture, "send_message", uncertain)
    receipt = {}
    assert not email_sender.invia_email_multi_allegati([str(pdf)], delivery_receipt=receipt)
    assert receipt["email_status"] == "uncertain"
    assert receipt["smtp_state"] == "sending"
    assert receipt["email_error"]


def test_sender_auth_failure_is_retryable_not_ambiguous(tmp_path, smtp, monkeypatch):
    messages, capture = smtp
    pdf = tmp_path / "research.pdf"
    pdf.write_bytes(b"synthetic")
    def refused(*args):
        raise smtplib.SMTPAuthenticationError(535, b"Synthetic auth failure")
    monkeypatch.setattr(capture, "login", refused)
    receipt = {}
    assert not email_sender.invia_email_multi_allegati([str(pdf)], delivery_receipt=receipt)
    assert receipt["email_status"] == "failed"
    assert messages == []


@pytest.mark.parametrize("judgment", ["favorable", "rejected", "incomplete"])
def test_research_pdf_has_ten_real_analytical_pages_and_mime_identical_bytes(tmp_path, smtp, judgment):
    from pypdf import PdfReader
    from trade_idea_fixtures import research_result, run_record
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, send_trade_idea_delivery
    result, run = research_result(judgment), run_record()
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    run['analysis_mode'] = RESEARCH_ANALYSIS_MODE
    manifest = prepare_trade_idea_delivery(run, result,
        output_dir=tmp_path / judgment, model_roots=[tmp_path], language="en")
    assert manifest["pdf_quality"]["status"] == "ready", manifest["pdf_quality"]
    assert manifest["pdf_quality"]["analytical_pages"] >= 10
    assert manifest["model_status"] == "not_required"
    assert manifest["omissions"] == []
    outcome = send_trade_idea_delivery(manifest)
    if judgment == 'incomplete':
        assert outcome['status'] == 'blocked'
        assert manifest['email_status'] == 'blocked'
        assert smtp[0] == []
        return
    assert outcome["status"] == "accepted"
    message = smtp[0][0]
    parts = list(message.iter_attachments())
    assert len(parts) == 1 and parts[0].get_content_type() == "application/pdf"
    raw = parts[0].get_payload(decode=True)
    assert sha256(raw).hexdigest() == manifest["expected_hashes"][manifest["attachments"][0]]
    assert raw == Path(manifest["attachments"][0]).read_bytes()
    assert 'Partial research' not in str(message['Subject'])
    text = "\n".join(page.extract_text() for page in PdfReader(manifest["attachments"][0]).pages)
    assert result["pm_view"] in text
    assert result["dossier"][-1]["paragraphs"][0][:60] in text
    assert judgment.title() in str(message["Subject"])


def test_short_partial_research_remains_partial_and_is_not_padded(tmp_path, smtp):
    from trade_idea_fixtures import research_result, run_record
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, send_trade_idea_delivery
    result = research_result("incomplete")
    result["dossier"] = [dict(result["dossier"][0], paragraphs=["Analysis stopped after the first archived source."])]
    manifest = prepare_trade_idea_delivery(run_record(), result, output_dir=tmp_path, model_roots=[tmp_path], language="en")
    assert manifest["pdf_quality"]["analytical_pages"] < 10
    assert manifest["artifact_status"] == "partial"
    assert manifest["pdf_quality"]["total_pages"] < 10
    assert manifest['email_status'] == 'blocked'
    assert send_trade_idea_delivery(manifest)["status"] == "blocked"
    assert smtp[0] == []


@pytest.mark.parametrize("language,kind,label", [
    ("it", "research", "In ricerca"), ("en", "research", "In Research"),
    ("it", "dcn", "Proposta in DCN"), ("en", "dcn", "Pending DCN proposal"),
])
def test_email_separates_favorable_judgment_from_persisted_destination(tmp_path, smtp, language, kind, label):
    from trade_idea_fixtures import research_result, run_record
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, send_trade_idea_delivery
    run = run_record()
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    run['analysis_mode'] = RESEARCH_ANALYSIS_MODE
    run["destination"] = {"kind": kind, "reason": "Synthetic verified routing reason", "decision_id": 17}
    result = research_result("favorable")
    manifest = prepare_trade_idea_delivery(run, result, output_dir=tmp_path,
                                          model_roots=[tmp_path], language=language)
    assert manifest["destination"] == run["destination"]
    assert send_trade_idea_delivery(manifest)["status"] == "accepted"
    message = smtp[0][0]
    assert label in str(message["Subject"])
    assert ("Favorevole" if language == "it" else "Favorable") in str(message["Subject"])
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert label in body and run["destination"]["reason"] in body


def test_package_recovery_preserves_pdf_and_rejects_changed_result(tmp_path):
    from trade_idea_fixtures import research_result, run_record
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, recover_trade_idea_delivery
    result = research_result("rejected")
    manifest = prepare_trade_idea_delivery(run_record(), result, output_dir=tmp_path, model_roots=[tmp_path])
    recovered = recover_trade_idea_delivery(tmp_path / "delivery.json", run_id=result["run_id"], ticker=result["ticker"], result=result)
    assert recovered == manifest
    result["summary"] = "A changed conclusion cannot reuse the original receipt."
    with pytest.raises(ValueError, match="different analytical result"):
        recover_trade_idea_delivery(tmp_path / "delivery.json", run_id=result["run_id"], ticker=result["ticker"], result=result)


@pytest.mark.parametrize("failure", [None, "modified_original", "modified_backup", "outside_roots"])
def test_exact_artifact_vault_recovers_only_original_missing_bytes(tmp_path, failure):
    from bellomberg.reporting.exact_artifacts import preserve_exact_artifacts, restore_exact_artifacts
    original = tmp_path / "original"
    original.mkdir()
    first, second = original / "memo.md", original / "model.payload.json"
    first.write_text("Saved paid conclusion, preserved verbatim", encoding="utf-8")
    second.write_text('{"generation_id":"frozen-generation","currency":"EUR"}', encoding="utf-8")
    contents = {str(path): path.read_bytes() for path in (first, second)}
    rows = [{"path": path, "sha256": sha256(data).hexdigest(), "kind": "frozen"} for path, data in contents.items()]
    vault = tmp_path / "vault"
    saved = preserve_exact_artifacts(rows, vault, allowed_roots=[tmp_path])
    first.unlink()
    if failure == "modified_original":
        second.write_bytes(b"Edited locally; never replace")
    elif failure == "modified_backup":
        (vault / (rows[0]["sha256"] + ".blob")).write_bytes(b"Corrupted backup")
    elif failure == "outside_roots":
        saved[0]["path"] = str(tmp_path.parent / "outside-exact-canary.md")
    if failure:
        with pytest.raises(ValueError):
            restore_exact_artifacts(saved, vault, allowed_roots=[tmp_path])
        assert not first.exists(), "Validate the complete package before any restore"
        if failure == "modified_original":
            assert second.read_bytes() == b"Edited locally; never replace"
    else:
        recovered = restore_exact_artifacts(saved, vault, allowed_roots=[tmp_path])
        assert recovered["restored"] == [str(first)]
        assert {path: Path(path).read_bytes() for path in contents} == contents
        assert restore_exact_artifacts(saved, vault, allowed_roots=[tmp_path])["restored"] == []


def test_exact_artifact_restore_preserves_concurrent_destination_and_removes_temp(tmp_path, monkeypatch):
    from bellomberg.reporting import exact_artifacts
    original = tmp_path / "memo.md"
    original.write_bytes(b"Frozen original conclusion")
    rows = [{"path": str(original), "sha256": sha256(original.read_bytes()).hexdigest(), "kind": "memo"}]
    vault = tmp_path / "vault"
    saved = exact_artifacts.preserve_exact_artifacts(rows, vault, allowed_roots=[tmp_path])
    original.unlink()
    real_link, attempts = exact_artifacts.os.link, []

    def concurrent_create(source, destination):
        attempts.append(str(destination))
        Path(destination).write_bytes(b"Concurrent original must survive")
        return real_link(source, destination)

    monkeypatch.setattr(exact_artifacts.os, "link", concurrent_create)
    with pytest.raises(ValueError, match="existing bytes preserved"):
        exact_artifacts.restore_exact_artifacts(saved, vault, allowed_roots=[tmp_path])
    assert attempts == [str(original)]
    assert original.read_bytes() == b"Concurrent original must survive"
    assert list(tmp_path.glob(".exact-*.tmp")) == []


def test_pdf_mutation_after_manifest_blocks_before_smtp(tmp_path, smtp):
    from trade_idea_fixtures import research_result, run_record
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, send_trade_idea_delivery
    manifest = prepare_trade_idea_delivery(run_record(), research_result(), output_dir=tmp_path, model_roots=[tmp_path])
    path = Path(manifest["attachments"][0])
    path.write_bytes(path.read_bytes() + b"changed")
    assert send_trade_idea_delivery(manifest)["status"] == "blocked"
    assert smtp[0] == []


@pytest.mark.parametrize("judgment", ["favorable", "rejected"])
def test_common_generator_excel_is_identical_in_download_inventory_and_mime(contract_tools, tmp_path, smtp, judgment):
    from openpyxl import load_workbook
    from trade_idea_fixtures import research_result, run_record, bind_workbook
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, send_trade_idea_delivery
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)
    assert workbook["valuation_usability"]["usable"] is True
    path = Path(workbook["path"])
    book = load_workbook(path, data_only=False)
    assert sum(cell.data_type == "f" for sheet in book for row in sheet for cell in row) > 0
    book.close()
    # Unrelated files, even newer ones in the same folder, are not candidates.
    (tmp_path / "OTHER-ISSUER.xlsx").write_bytes(b"unrelated generation")
    result = bind_workbook(research_result(judgment), workbook)
    manifest = prepare_trade_idea_delivery(run_record(), result, {"SYNTH-EXT": workbook},
        output_dir=tmp_path / "delivery", model_roots=[tmp_path], language="en")
    assert manifest["model_status"] == "ready"
    assert len(manifest["attachments"]) == 2
    assert send_trade_idea_delivery(manifest)["status"] == "accepted"
    parts = {part.get_filename(): part.get_payload(decode=True) for part in smtp[0][0].iter_attachments()}
    assert set(parts) == {"trade-idea.pdf", path.name}
    assert parts[path.name] == path.read_bytes()
    excel = next(a for a in manifest["artifacts"] if a["kind"] == "xlsx")
    assert excel["sha256"] == sha256(parts[path.name]).hexdigest() == workbook["workbook_sha256"]
    assert manifest["valuations"][0]["snapshot_id"] == workbook["snapshot_id"]
    assert manifest["valuations"][0]["generation_id"] == workbook["generation_id"]


@pytest.mark.parametrize("kind", ["pdf", "xlsx"])
def test_mutation_between_manifest_check_and_mime_read_is_blocked(contract_tools, tmp_path, smtp, monkeypatch, kind):
    from trade_idea_fixtures import research_result, run_record, bind_workbook
    from bellomberg.reporting.trade_idea_delivery import prepare_trade_idea_delivery, send_trade_idea_delivery
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)
    manifest = prepare_trade_idea_delivery(run_record(), bind_workbook(research_result(), workbook), {"SYNTH-EXT": workbook},
        output_dir=tmp_path / "delivery", model_roots=[tmp_path])
    original_sender = email_sender.invia_email_multi_allegati
    def mutate_then_build(*args, **kwargs):
        path = Path(next(a["path"] for a in manifest["artifacts"] if a["kind"] == kind))
        path.write_bytes(path.read_bytes() + b"changed between validation and MIME")
        return original_sender(*args, **kwargs)
    monkeypatch.setattr(email_sender, "invia_email_multi_allegati", mutate_then_build)
    outcome = send_trade_idea_delivery(manifest)
    assert outcome["status"] == "blocked"
    assert smtp[0] == []


@pytest.mark.parametrize("field", ["snapshot_id", "generation_id", "valuation_date"])
def test_workbook_must_match_generation_reviewed_by_capo(contract_tools, tmp_path, field):
    from trade_idea_fixtures import research_result, bind_workbook
    from bellomberg.reporting.trade_idea_delivery import _candidate_workbooks
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)
    result = bind_workbook(research_result(), workbook)
    result["valuation_refs"][0][field] = "different-generation-or-cutoff"
    checked = _candidate_workbooks("SYNTH-EXT", [workbook], (), [tmp_path], result["valuation_refs"])
    assert checked["attachments"] == []
    assert checked["valuations"][0]["status"] == "analysis_mismatch"


def test_later_failed_attempt_does_not_hide_a_reviewed_valid_workbook(contract_tools, tmp_path):
    from trade_idea_fixtures import research_result, bind_workbook
    from bellomberg.reporting.trade_idea_delivery import _candidate_workbooks
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)
    result = bind_workbook(research_result(), workbook)
    failed = {"ticker": "SYNTH-EXT", "error": "Later source retrieval failed",
              "valuation_usability": {"usable": False}}
    checked = _candidate_workbooks("SYNTH-EXT", [workbook, workbook, failed], (), [tmp_path], result["valuation_refs"])
    assert checked["attachments"] == [str(Path(workbook["path"]).resolve())]
    assert [row["status"] for row in checked["valuations"]] == ["ready", "incomplete"]
    assert checked["valuations"][1]["reason"] == "Later source retrieval failed"


@pytest.mark.parametrize("field", ["valuation_date", "fair_value_base", "currency"])
def test_payload_and_capo_cannot_relabel_the_workbook_basis(contract_tools, tmp_path, field):
    from copy import deepcopy
    from trade_idea_fixtures import research_result, bind_workbook
    from bellomberg.reporting.trade_idea_delivery import _candidate_workbooks
    chat_tools, _, _ = contract_tools
    _, workbook = _call(chat_tools)
    altered = deepcopy(workbook)
    altered[field] = {"valuation_date": "2099-01-01", "fair_value_base": 999999.0, "currency": "USD"}[field]
    result = bind_workbook(research_result(), altered)
    checked = _candidate_workbooks("SYNTH-EXT", [altered], (), [tmp_path], result["valuation_refs"])
    assert checked["attachments"] == []
    assert checked["valuations"][0]["status"] == "model_basis_mismatch"


def test_routing_reason_does_not_change_analytical_qualification(tmp_path):
    from copy import deepcopy
    from trade_idea_fixtures import research_result, run_record
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    result = research_result("rejected")
    result["destination"] = {"kind": "none", "reason": "Not routed yet"}
    preview = build_trade_idea_report(run_record(), result, output_path=tmp_path / "preview.pdf")
    final = deepcopy(result)
    final["destination"] = {"kind": "research", "reason": "A changed book blocks an operational proposal. " * 90}
    published = build_trade_idea_report(run_record(), final, output_path=tmp_path / "final.pdf")
    assert preview["quality"]["status"] == published["quality"]["status"] == "ready"
    assert preview["quality"]["analytical_pages"] == published["quality"]["analytical_pages"]
