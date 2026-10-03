"""Preserve and restore the exact bytes of an already attested run artifact.

This is storage only. It never renders, recompiles, chooses a new generation,
or replaces an existing file whose bytes differ from the original receipt.
"""
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


def _paths(receipts, vault_dir, allowed_roots):
    roots = [Path(root).resolve() for root in allowed_roots]
    if not roots:
        raise ValueError("Exact artifact storage requires explicit allowed roots")
    vault = Path(vault_dir).resolve()
    if not any(vault.is_relative_to(root) for root in roots):
        raise ValueError("Exact artifact vault is outside the allowed roots")
    rows, seen = [], {}
    for source in receipts:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise ValueError("Exact artifact receipt requires a path")
        digest = source.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Exact artifact receipt requires a SHA-256")
        path = Path(source["path"]).resolve()
        if not any(path.is_relative_to(root) for root in roots) or path.is_relative_to(vault):
            raise ValueError("Exact artifact destination is outside the permitted artifact roots")
        if path in seen:
            if seen[path] != digest:
                raise ValueError("Conflicting exact artifact receipts for one destination")
            continue
        seen[path] = digest
        rows.append({**deepcopy(source), "path": str(path)})
    return rows, vault


def _read(path, digest):
    content = path.read_bytes()
    if sha256(content).hexdigest() != digest:
        raise ValueError("Exact artifact hash differs; existing bytes preserved: " + path.name)
    return content


def _create_exact(path, content, digest):
    """Publish durable bytes only if the destination is still absent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".exact-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Unlike replace(), a hard-link publish never overwrites a file
            # created by another worker between validation and publication.
            os.link(temporary, path)
            return True
        except FileExistsError:
            _read(path, digest)
            return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def preserve_exact_artifacts(receipts, vault_dir, *, allowed_roots):
    rows, vault = _paths(receipts, vault_dir, allowed_roots)
    content = [(row, _read(Path(row["path"]), row["sha256"])) for row in rows]
    for row, data in content:
        saved = vault / (row["sha256"] + ".blob")
        if saved.exists():
            _read(saved, row["sha256"])
        else:
            _create_exact(saved, data, row["sha256"])
    return rows


def restore_exact_artifacts(receipts, vault_dir, *, allowed_roots):
    rows, vault = _paths(receipts, vault_dir, allowed_roots)
    pending, verified = [], []
    # Validate every existing destination and every needed backup before the
    # first write, so an already modified workbook prevents a partial restore.
    for row in rows:
        path, digest = Path(row["path"]), row["sha256"]
        if path.exists():
            _read(path, digest)
            verified.append(str(path))
        else:
            pending.append((path, digest, _read(vault / (digest + ".blob"), digest)))
    restored = []
    for path, digest, content in pending:
        if _create_exact(path, content, digest):
            restored.append(str(path))
        else:
            verified.append(str(path))
    return {"restored": restored, "verified": verified}
