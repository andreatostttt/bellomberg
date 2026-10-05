"""Descriptor-relative filesystem checks; no application or external writes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.testing import offline_pytest as runner


def _rilancia_sotto_runner_offline(tmp_path, nome_test):
    """Rilancia UN test di questo file sotto tools/testing/offline_pytest.py ed esige che
    li' passi. Vale per i test della guardia di audit, che solo il runner installa."""
    root = Path(__file__).resolve().parent.parent
    nodo = Path(__file__).resolve().as_posix() + "::" + nome_test
    esito = subprocess.run(
        [sys.executable, "-I", str(root / "tools" / "testing" / "offline_pytest.py"),
         "--output-root", str(tmp_path / "offline-evidence"), nodo,
         "-q", "-p", "no:cacheprovider"],
        cwd=root, capture_output=True, encoding="utf-8", errors="replace", timeout=600)
    uscita = esito.stdout + esito.stderr
    assert esito.returncode == 0 and "1 passed" in uscita, uscita[-3000:]


def test_pending_open_target_cannot_authorize_a_second_audit_event(tmp_path):
    # Questo test prova la guardia di audit che SOLO tools/testing/offline_pytest.py
    # installa (sys.addaudithook prima di ogni import). Nel pytest nudo della suite la
    # guardia non c'e': il test cadeva con KeyError su BELLOMBERG_PROJECT_ROOT o con
    # 'unexpectedly_allowed' — rosso d'ambiente, non della guardia (ZR 05/10, classe A).
    # Niente skip: fuori dal sandbox il test RILANCIA se stesso sotto il runner offline
    # (un hook di audit non si rimuove, installarlo qui avvelenerebbe il resto della
    # suite) ed esige che li' passi. La garanzia si misura in ogni modo in cui si lancia.
    if not os.environ.get("BELLOMBERG_OFFLINE_TEST_SANDBOX"):
        _rilancia_sotto_runner_offline(
            tmp_path, "test_pending_open_target_cannot_authorize_a_second_audit_event")
        return
    allowed = tmp_path / "allowed-canary.txt"
    outside = Path(os.environ["BELLOMBERG_PROJECT_ROOT"]) / "__audit_only_outside_canary__"
    outcomes = []
    nested = False

    def observe(event, values):
        nonlocal nested
        if (event != "open" or nested or not isinstance(values[0], (str, bytes))
                or os.fsdecode(values[0]) != str(allowed)):
            return
        nested = True
        try:
            # Audit-only event; a defective guard still cannot write outside.
            sys.audit("open", str(outside), None, os.O_WRONLY | os.O_CREAT)
        except BaseException as exc:
            outcomes.append(type(exc).__name__)
        else:
            outcomes.append("unexpectedly_allowed")
        finally:
            nested = False

    sys.addaudithook(observe)
    descriptor = os.open(allowed, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(descriptor)
    assert outcomes == ["OfflineViolation"], outcomes


def test_relative_descriptor_paths_resolve_against_verified_directory(tmp_path, monkeypatch):
    directory = tmp_path / "nested"
    directory.mkdir()
    info = directory.stat()
    original = os.fstat
    monkeypatch.setattr(os, "fstat", lambda fd: info if fd == 654321 else original(fd))
    known = {654321: (str(directory), (info.st_dev, info.st_ino))}
    assert runner._resolve_audit_path("rehearsal.db", 654321, known) == os.path.normcase(
        str(directory / "rehearsal.db"))
    # Absolute paths ignore dir_fd according to the native OS contract.
    assert runner._resolve_audit_path(str(directory), 999999, known) == os.path.normcase(str(directory))
    with pytest.raises(runner.OfflineViolation, match="descriptor"):
        runner._resolve_audit_path("rehearsal.db", 999999, known)
    known[654321] = (str(directory), (info.st_dev, info.st_ino + 1))
    with pytest.raises(runner.OfflineViolation, match="descriptor"):
        runner._resolve_audit_path("rehearsal.db", 654321, known)
    known[654321] = (str(directory), (info.st_dev, info.st_ino))
    directory.rename(tmp_path / "moved")
    directory.mkdir()
    with pytest.raises(runner.OfflineViolation, match="descriptor"):
        runner._resolve_audit_path("rehearsal.db", 654321, known)


def test_real_posix_relative_mutations_and_recursive_cleanup(tmp_path):
    if os.name != "posix":
        pytest.skip("Native dir_fd operations require a POSIX runner")
    assert shutil.rmtree.avoids_symlink_attacks
    folder = tmp_path / "work"
    folder.mkdir()
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.mkdir("nested", dir_fd=descriptor)
        child = os.open("nested", os.O_RDONLY, dir_fd=descriptor)
        try:
            file_descriptor = os.open("rehearsal.db", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=child)
            os.close(file_descriptor)
            os.chmod("rehearsal.db", 0o600, dir_fd=child)
            os.utime("rehearsal.db", dir_fd=child)
            os.rename("rehearsal.db", "moved.db", src_dir_fd=child, dst_dir_fd=descriptor)
            os.link("moved.db", "copy.db", src_dir_fd=descriptor, dst_dir_fd=child)
            os.symlink("moved.db", "link.db", dir_fd=descriptor)
            os.symlink("../moved.db", "nested/sibling-link.db", dir_fd=descriptor)
            assert (folder / "nested/sibling-link.db").resolve() == folder / "moved.db"
            os.unlink("sibling-link.db", dir_fd=child)
        finally:
            os.close(child)
        os.unlink("link.db", dir_fd=descriptor)
        os.unlink("moved.db", dir_fd=descriptor)
    finally:
        os.close(descriptor)
    shutil.rmtree(folder)
    assert not folder.exists()


def test_outside_directory_descriptor_cannot_delete_or_rename(tmp_path):
    if os.name != "posix":
        pytest.skip("Native directory descriptors require a POSIX runner")
    # Stessa guardia di audit del test sopra: nel pytest nudo (CI suite-linux) nessun hook
    # nega la scrittura e il test cadeva con «DID NOT RAISE» (LINUX 05/10, classe A).
    # Fuori dal sandbox si rilancia sotto il runner offline ed esige che li' passi.
    if not os.environ.get("BELLOMBERG_OFFLINE_TEST_SANDBOX"):
        _rilancia_sotto_runner_offline(tmp_path, "test_outside_directory_descriptor_cannot_delete_or_rename")
        return
    outside = Path(__file__).resolve().parent
    descriptor = os.open(outside, os.O_RDONLY)
    local = os.open(tmp_path, os.O_RDONLY)
    try:
        # Audit-only probes: even a faulty guard cannot modify repository files.
        for event, values in (
            ("os.remove", ("never-created.txt", descriptor)),
            ("os.rename", ("never-created.txt", "target.txt", local, descriptor)),
            ("os.rename", ("never-created.txt", "target.txt", descriptor, local)),
            ("os.link", ("never-created.txt", "target.txt", local, descriptor)),
            ("os.link", ("never-created.txt", "target.txt", descriptor, local)),
            ("os.symlink", ("target.txt", "never-created.txt", descriptor)),
            ("os.remove", (os.path.relpath(outside / "never-created.txt", tmp_path), local)),
            ("os.symlink", (str(outside / "target.txt"), "never-created.txt", local)),
        ):
            with pytest.raises(BaseException, match="offline isolation blocked filesystem_write"):
                sys.audit(event, *values)
    finally:
        os.close(local)
        os.close(descriptor)
    with pytest.raises(BaseException, match="filesystem_descriptor"):
        sys.audit("os.remove", "never-created.txt", descriptor)
