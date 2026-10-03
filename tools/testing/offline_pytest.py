"""Run existing pytest suites with isolation installed before application imports.

No application launcher: this is a test-only entry point. All process launches,
network access, personal files and SQLite databases outside the fresh sandbox
are denied, including during collection. Receipts contain measured counters.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import platform
from pathlib import Path
import socket
import site
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading


class OfflineViolation(BaseException):
    """Cannot be swallowed by application `except Exception` fallbacks."""


def _resolve_audit_path(path, dir_fd, descriptors):
    """Resolve native descriptor-relative paths; unknown/reused fds fail closed."""
    def descriptor_path(fd, *, directory=False):
        try:
            saved_path, identity = descriptors[fd]
            opened, named = os.fstat(fd), os.stat(saved_path)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise OfflineViolation(f"unverified filesystem descriptor: {fd}") from exc
        if (identity != (opened.st_dev, opened.st_ino)
                or identity != (named.st_dev, named.st_ino)
                or (directory and not stat.S_ISDIR(opened.st_mode))):
            raise OfflineViolation(f"changed filesystem descriptor: {fd}")
        return saved_path

    if isinstance(path, int):
        path = descriptor_path(path)
    else:
        path = os.fsdecode(path)
        if not os.path.isabs(path) and dir_fd not in (None, -1):
            path = os.path.join(descriptor_path(dir_fd, directory=True), path)
    return os.path.normcase(os.path.realpath(path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--self-test-only", action="store_true")
    args, pytest_args = parser.parse_known_args(argv)
    root = Path(__file__).resolve().parents[2]
    dependency_site = site.getusersitepackages()
    sandbox = Path(args.output_root).resolve()
    if sandbox.exists():
        parser.error("--output-root must be a new directory (evidence is never overwritten)")
    sandbox.mkdir(parents=True)
    for folder in ("tmp", "data", "report", "research_notes", "home", "cache"):
        (sandbox / folder).mkdir()

    # Keep OS operation variables only, not credentials or the user's settings.
    keep = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE",
        "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "PROGRAMDATA"}}
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update({
        "BELLOMBERG_PROJECT_ROOT": str(root),
        "BELLOMBERG_DATA_DIR": str(sandbox / "data"),
        "BELLOMBERG_REPORT_DIR": str(sandbox / "report"),
        "BELLOMBERG_RESEARCH_NOTES_DIR": str(sandbox / "research_notes"),
        "HOME": str(sandbox / "home"), "USERPROFILE": str(sandbox / "home"),
        "LOCALAPPDATA": str(sandbox / "cache"), "APPDATA": str(sandbox / "cache"),
        "XDG_CACHE_HOME": str(sandbox / "cache"), "MPLCONFIGDIR": str(sandbox / "cache"),
        "TEMP": str(sandbox / "tmp"), "TMP": str(sandbox / "tmp"),
        "TMPDIR": str(sandbox / "tmp"), "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "BELLOMBERG_OFFLINE_TEST_SANDBOX": str(sandbox),
        "ANONYMIZED_TELEMETRY": "False",
    })
    # A caller may have resolved the OS temp directory before isolation.
    # Reset its cached value as well as TEMP/TMP before pytest capture opens files.
    tempfile.tempdir = str(sandbox / "tmp")
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(root / "src"), str(root), str(root / "tests")]
    # -I skips user site startup hooks. Dependencies may still be installed there;
    # add the directory only, never execute its .pth startup code.
    sys.path.append(dependency_site)
    if any(name == "bellomberg" or name.startswith("bellomberg.") for name in sys.modules):
        raise RuntimeError("application imported before offline isolation")
    # matplotlib discovers system fonts on first use (fc-list on Linux/macOS). Build that
    # cache once, inside the sandbox (MPLCONFIGDIR above), before subprocesses are denied:
    # a local font query, never network, and no application module is imported here.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.font_manager  # noqa: F401
    except Exception as exc:  # declared, the suite then reports its own failures
        print("matplotlib font cache not prepared: " + type(exc).__name__ + ": " + str(exc)[:200],
              file=sys.stderr)
    counters = collections.Counter()
    blocked = []
    local = threading.local()
    descriptors = {}

    def canonical(path, dir_fd=None):
        return _resolve_audit_path(path, dir_fd, descriptors)

    allowed = canonical(sandbox)
    personal = [canonical(root / part) for part in
                ("data", "report", "research_notes", "models", "data_pre_junction", "portfolio.json")]
    # canonical(root / "data") resolves junctions as well as ordinary directories.
    platform_file = canonical(platform.__file__)

    def platform_version_probe():
        frame = sys._getframe(1)
        while frame is not None:
            if (frame.f_code.co_name == '_syscmd_ver'
                    and canonical(frame.f_code.co_filename) == platform_file):
                return True
            frame = frame.f_back
        return False

    def within(path, parent):
        return path == parent or path.startswith(parent + os.sep)

    def deny(kind, target, exception=OfflineViolation):
        counters["blocked_" + kind] += 1
        blocked.append({"kind": kind, "target": str(target)[:240]})
        raise exception(f"offline isolation blocked {kind}: {target}")

    def checked_path(path, *, write=False, dir_fd=None):
        if path is None:
            return
        # open(fd) may wrap existing stdin/stdout; descriptor mutations must be verified.
        if isinstance(path, int) and not write:
            return
        try:
            resolved = canonical(path, dir_fd)
        except OfflineViolation as exc:
            deny("filesystem_descriptor", str(exc))
        if resolved == canonical(os.devnull):
            return
        if not within(resolved, allowed):
            if any(within(resolved, item) for item in personal):
                deny("personal_file", resolved)
            if Path(resolved).name == ".env":
                deny("secrets", resolved)
            if write:
                deny("filesystem_write", resolved)
        if write:
            counters["allowed_filesystem_writes"] += 1

    def audit(event, values):
        if getattr(local, "inside", False):
            return
        local.inside = True
        try:
            if (getattr(local, "socketpair", False) and event in {"socket.bind", "socket.connect"}
                    and isinstance(values[1], tuple) and values[1][0] in {"127.0.0.1", "::1"}):
                counters["local_socketpair_" + event.split(".")[1]] += 1
            elif event == "socket.bind":
                # urllib3 probes IPv6 support at import, catching OSError.
                # Deny the probe before the OS bind, without aborting collection.
                deny("network", event, OSError)
            elif event in {"socket.connect", "socket.connect_ex",
                         "socket.getaddrinfo", "socket.sendto", "smtplib.connect"}:
                deny("network", event)
            elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
                           "os.spawn", "os.startfile"}:
                # Python's Windows platform probe may try `ver` after a local
                # WMI failure. Still block it before creation; OSError lets the
                # stdlib use native sys.getwindowsversion, without a fake OS.
                if event == 'subprocess.Popen' and platform_version_probe():
                    deny("subprocess", 'stdlib platform._syscmd_ver', OSError)
                deny("subprocess", event)
            elif event in {"sqlite3.enable_load_extension", "sqlite3.load_extension"}:
                deny("sqlite_extension", event)
            elif event == "sqlite3.connect":
                target = os.fsdecode(values[0])
                if target != ":memory:":
                    if target.startswith("file:"):
                        from urllib.parse import unquote
                        target = unquote(target[5:].split("?", 1)[0])
                        if os.name == 'nt' and len(target.lstrip('/')) > 2 and target.lstrip('/')[1] == ':':
                            target = target.lstrip('/')
                    if not within(canonical(target), allowed):
                        deny("sqlite", target)
                counters["allowed_sqlite_connections"] += 1
            elif event == "open":
                path, mode, flags = values
                pending = getattr(local, "open_target", None)
                if pending is not None:
                    # Consume before another audit observer can emit a nested open.
                    del local.open_target
                    if path == pending[0]:
                        path = pending[1]
                checked_path(path, write=bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT |
                                                       os.O_TRUNC | os.O_APPEND)))
            elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.chown"}:
                checked_path(values[0], write=True, dir_fd=values[-1])
            elif event in {"os.rename", "os.link"}:
                checked_path(values[0], write=True, dir_fd=values[2])
                checked_path(values[1], write=True, dir_fd=values[3])
            elif event == "os.symlink":
                destination = canonical(values[1], values[2])
                checked_path(destination, write=True)
                target = os.fsdecode(values[0])
                if not os.path.isabs(target):
                    target = os.path.join(os.path.dirname(destination), target)
                checked_path(target, write=True)
            elif event in {"os.truncate", "os.chflags"}:
                checked_path(values[0], write=True)
        finally:
            local.inside = False

    sys.addaudithook(audit)
    # CPython's open audit event omits dir_fd. Preserve the native openat call,
    # expose its resolved target only during the synchronous audit, and track
    # the resulting descriptor so rmtree/unlink/rename can verify their bases.
    original_open = os.open

    def descriptor_open(path, flags, mode=0o777, *, dir_fd=None):
        path = os.fspath(path)
        target = canonical(path, dir_fd)
        previous = getattr(local, "open_target", None)
        local.open_target = (path, target)
        try:
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        finally:
            if previous is None:
                if hasattr(local, "open_target"):
                    del local.open_target
            else:
                local.open_target = previous
        info = os.fstat(descriptor)
        descriptors[descriptor] = (target, (info.st_dev, info.st_ino))
        return descriptor

    os.open = descriptor_open
    if original_open in os.supports_dir_fd:
        # shutil checks this membership when selecting its symlink-safe rmtree.
        os.supports_dir_fd.add(descriptor_open)
    # Windows implements socketpair with private loopback sockets. asyncio needs
    # these for its self-pipe; allow only the stdlib's synchronous pair constructor.
    original_socketpair = socket.socketpair

    def local_socketpair(*positional, **keywords):
        local.socketpair = True
        try:
            return original_socketpair(*positional, **keywords)
        finally:
            local.socketpair = False

    socket.socketpair = local_socketpair
    # curl_cffi uses native sockets, which do not emit Python socket audit events.
    # Guard its dispatch entry points too, before yfinance/application imports.
    try:
        import curl_cffi
    except ImportError:
        curl_cffi = None
    if curl_cffi is not None:
        def blocked_curl(*positional, **keywords):
            deny("network", "curl_cffi.perform")
        curl_cffi.Curl.perform = blocked_curl
        curl_cffi.AsyncCurl.add_handle = blocked_curl
    # dotenv imports are safe now. Allow fixture .env files in the sandbox only.
    import dotenv
    import dotenv.main
    original_load = dotenv.main.load_dotenv
    original_values = dotenv.main.dotenv_values

    def load_fixture_env(dotenv_path=None, *positional, **keywords):
        if dotenv_path is None or not within(canonical(dotenv_path), allowed):
            counters["dotenv_loads_suppressed"] += 1
            return False
        return original_load(dotenv_path, *positional, **keywords)

    dotenv.load_dotenv = dotenv.main.load_dotenv = load_fixture_env

    def fixture_env_values(dotenv_path=None, *positional, **keywords):
        if dotenv_path is None or not within(canonical(dotenv_path), allowed):
            counters["dotenv_values_suppressed"] += 1
            return {}
        return original_values(dotenv_path, *positional, **keywords)

    dotenv.dotenv_values = dotenv.main.dotenv_values = fixture_env_values
    connections = []
    closed_changes = []

    class MeasuredConnection(sqlite3.Connection):
        def close(self):
            try:
                changes = self.total_changes
            except sqlite3.ProgrammingError:
                return super().close()
            super().close()
            closed_changes.append(changes)

    original_connect = sqlite3.connect

    def measured_connect(*positional, **keywords):
        # Preserve explicit connection factories used by test subjects.
        if len(positional) < 6 and "factory" not in keywords:
            keywords["factory"] = MeasuredConnection
        connection = original_connect(*positional, **keywords)
        def authorize(action, first, second, database, trigger):
            if action == sqlite3.SQLITE_ATTACH:
                target = str(first or '')
                if target.startswith('file:'):
                    from urllib.parse import unquote
                    target = unquote(target[5:].split('?', 1)[0])
                    if os.name == 'nt' and len(target.lstrip('/')) > 2 and target.lstrip('/')[1] == ':':
                        target = target.lstrip('/')
                if target != ':memory:' and not within(canonical(target), allowed):
                    counters['blocked_sqlite_attach'] += 1
                    blocked.append({'kind': 'sqlite_attach', 'target': target[:240]})
                    return sqlite3.SQLITE_DENY
                counters['allowed_sqlite_attach'] += 1
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        connections.append(connection)
        return connection

    sqlite3.connect = measured_connect
    sqlite3.dbapi2.connect = measured_connect
    canaries = {}
    probes = {
        "network": lambda: socket.create_connection(("192.0.2.1", 443)),
        "smtp": lambda: __import__("smtplib").SMTP("192.0.2.1", 25),
        "sqlite": lambda: sqlite3.connect(str(root / "data" / "consigliere.db")),
        "secrets": lambda: (root / ".env").read_bytes(),
        "write": lambda: (root / "offline-forbidden-canary.txt").write_text("forbidden"),
        "subprocess": lambda: subprocess.run([sys.executable, "-c", "pass"]),
    }
    if curl_cffi is not None:
        probes["native_curl"] = lambda: curl_cffi.Curl().perform()
    for name, probe in probes.items():
        try:
            probe()
        except OfflineViolation:
            canaries[name] = "blocked_before_access"
        else:
            raise RuntimeError("isolation canary unexpectedly passed: " + name)
    connection = sqlite3.connect(str(sandbox / "isolation-canary.db"))
    try:
        connection.execute('ATTACH DATABASE ? AS forbidden', (str(root / 'data' / 'consigliere.db'),))
    except sqlite3.DatabaseError:
        canaries['sqlite_attach'] = 'blocked_before_access'
    else:
        raise RuntimeError('isolation canary unexpectedly passed: sqlite_attach')
    if os.name == 'nt':
        before_platform = counters['blocked_subprocess']
        platform._syscmd_ver()
        if counters['blocked_subprocess'] <= before_platform:
            raise RuntimeError('platform subprocess isolation canary did not exercise the guard')
        canaries['platform_subprocess'] = 'blocked_before_access; native OS query remains available'
    canary_counters = dict(counters)
    connection.execute("CREATE TABLE proof(value TEXT)")
    connection.execute("INSERT INTO proof VALUES ('measured')")
    connection.commit()
    assert connection.total_changes == 1
    connection.close()
    code = 0
    failure = None
    try:
        if not args.self_test_only:
            import pytest
            code = int(pytest.main(["-p", "no:cacheprovider", "--basetemp", str(sandbox / "pytest"),
                                    *pytest_args]))
    except BaseException as exc:
        code = 2
        failure = {"type": type(exc).__name__, "detail": str(exc)[:1000]}
        raise
    finally:
        changes = sum(closed_changes)
        for conn in connections:
            try:
                changes += conn.total_changes
            except sqlite3.ProgrammingError:
                pass
        receipt = {
            "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "sandbox": str(sandbox), "exit_code": code, "failure": failure,
            "installed_before_application_imports": True,
            "canaries": canaries, "canary_counters": canary_counters,
            "counters": dict(counters), "sqlite_total_changes_measured": changes,
            "blocked_attempts": blocked, "pytest_args": pytest_args,
            "subprocess_policy": "all blocked; no child may escape isolation",
        }
        (sandbox / "isolation-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print("OFFLINE_RECEIPT=" + str(sandbox / "isolation-receipt.json"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
