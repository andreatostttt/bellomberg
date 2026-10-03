"""Independent review findings: no replacement race and no unattested MIME files."""
from hashlib import sha256
from pathlib import Path

import pytest

from bellomberg.agents import consigliere_multi as cm
from bellomberg.agents.weekly_lifecycle import write_frozen_text, render_pdf_once
from bellomberg.reporting import exact_artifacts
from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
from test_cablaggio_consigliere_multi import run_offline
from test_weekly_recovery import _store


def test_same_frozen_markdown_is_not_replaced(tmp_path, monkeypatch):
    path = tmp_path / 'memo.md'
    path.write_bytes(b'original frozen memo')
    monkeypatch.setattr(exact_artifacts, '_create_exact', lambda *args:
                        pytest.fail('An existing identical original needs no write'))
    write_frozen_text(path, 'original frozen memo')
    assert path.read_bytes() == b'original frozen memo'


def test_original_created_during_atomic_publish_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / 'memo.md'
    original_link = exact_artifacts.os.link
    def pm_writes_before_publish(source, destination):
        Path(destination).write_bytes(b'PM annotation arrived concurrently')
        return original_link(source, destination)
    monkeypatch.setattr(exact_artifacts.os, 'link', pm_writes_before_publish)
    with pytest.raises(WeeklyRunBlocked, match='Originale modificato'):
        write_frozen_text(path, 'original frozen memo')
    assert path.read_bytes() == b'PM annotation arrived concurrently'
    assert not list(tmp_path.glob('.exact-*.tmp'))


def test_every_mime_attachment_uses_immutable_bundle_hash_and_blocks_changed_pdf(run_offline, monkeypatch):
    native_sender = cm._send_weekly_email
    captured = []
    def alter_before_mime(attachments, *args, expected_hashes=None, require_all_hashes=False, **kwargs):
        canonical = {str(Path(path).resolve()) for path in attachments}
        assert require_all_hashes is True and set(expected_hashes) == canonical
        assert all(sha256(Path(path).read_bytes()).hexdigest() == expected_hashes[str(Path(path).resolve())]
                   for path in attachments)
        pdf = next(Path(path) for path in attachments if str(path).endswith('.pdf'))
        pdf.write_bytes(b'%PDF-modified after bundle sealing')
        captured.append(pdf)
        return native_sender(attachments, *args, expected_hashes=expected_hashes,
                             require_all_hashes=require_all_hashes, **kwargs)
    monkeypatch.setattr(cm, '_send_weekly_email', alter_before_mime)
    with pytest.raises(WeeklyRunBlocked, match='Artefatto mancante o modificato'):
        cm.run_multi_agent(send_email=True)
    assert captured and run_offline.inviati == []
    assert captured[0].read_bytes() == b'%PDF-modified after bundle sealing'
    assert _store().status()['status'] == 'incomplete'


def _render_store(tmp_path, memo_id):
    from types import SimpleNamespace
    checkpoints = {}
    store = SimpleNamespace(db=SimpleNamespace(db_path=str(tmp_path / 'render-tests.sqlite')),
        memo_id=memo_id, run_id='synthetic-run-' + str(memo_id), get=checkpoints.get,
        complete=lambda stage, payload: checkpoints.setdefault(stage, payload))
    return store, SimpleNamespace(REPORT_DIR=tmp_path / 'reports')


def test_two_memos_render_real_pdfs_to_distinct_paths_and_reuse_original(tmp_path, monkeypatch):
    from bellomberg.reporting import pdf_institutional
    monkeypatch.setattr(pdf_institutional, 'REPORT_DIR', tmp_path / 'reports')
    first, module = _render_store(tmp_path, 1)
    second, _ = _render_store(tmp_path, 2)
    inputs = {'memo_markdown': '# Frozen synthetic memo\nSources and limits are explicit.',
              'portfolio_data': {'positions': []}, 'title_date': '2026-01-01'}
    a = render_pdf_once(first, module, pdf_institutional.build_institutional_memo, **inputs)
    before = Path(a).read_bytes()
    b = render_pdf_once(second, module, pdf_institutional.build_institutional_memo, **inputs)
    assert a != b and Path(a).read_bytes() == before
    assert before.startswith(b'%PDF-') and Path(b).stat().st_size > 1000
    def forbidden(**kwargs):
        pytest.fail('A rendered checkpoint must not render again')
    assert render_pdf_once(first, module, forbidden, **inputs) == a


def test_unattested_existing_pdf_is_never_opened_by_renderer(tmp_path):
    store, module = _render_store(tmp_path, 1)
    module.REPORT_DIR.mkdir()
    original = module.REPORT_DIR / 'weekly_1_synthetic-run-1_memo.pdf'
    original.write_bytes(b'%PDF-existing PM original')
    with pytest.raises(WeeklyRunBlocked, match='PDF originale non attestato'):
        render_pdf_once(store, module, lambda **kwargs: pytest.fail('renderer would overwrite original'))
    assert original.read_bytes() == b'%PDF-existing PM original'
