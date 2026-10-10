#!/usr/bin/env python3
"""Verify and apply exact Python source patches, with backups and rollback.

The manifest is a list of existing-file entries (file, sha256, patches of
old/new, result_sha256) and new-file entries (file, new_file, result_sha256). Existing files
are always reconstructed from their own verified contents. No environment,
credentials, service commands, or replacement whole-file source is read.
Without --apply, this command does not create backups, journals, or lock files.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import time
from typing import Optional


class ReleaseError(Exception):
    pass


@dataclass
class FilePlan:
    name: str
    path: Path
    original: Optional[bytes]
    result: bytes
    metadata: os.stat_result
    patch_count: int


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_digest(value, name: str, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise ReleaseError(f"{name}: invalid {field}")
    return value.lower()


def target_path(directory: Path, name: str) -> Path:
    if directory.is_symlink():
        raise ReleaseError("Source directory became a symlink")
    if not isinstance(name, str) or not name or "\\" in name:
        raise ReleaseError("Manifest file must be a nonempty relative POSIX path")
    relative = Path(name)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise ReleaseError(f"Unsafe manifest path: {name}")
    target = directory / relative
    # Reject symlinks, including parent components, instead of patching a link
    # target or replacing the link itself.
    cursor = directory
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ReleaseError(f"Symlink source path is not supported: {name}")
    if not target.parent.is_dir():
        raise ReleaseError(f"Source parent directory does not exist: {name}")
    return target


def prepare(manifest, directory: Path) -> list[FilePlan]:
    if not isinstance(manifest, list) or not manifest:
        raise ReleaseError("Manifest must be a nonempty JSON list")
    plans = []
    seen = set()
    for entry in manifest:
        if not isinstance(entry, dict):
            raise ReleaseError("Each manifest entry must be an object")
        name = entry.get("file")
        target = target_path(directory, name)
        if name in seen:
            raise ReleaseError(f"Duplicate manifest file: {name}")
        seen.add(name)
        if "new_file" in entry:
            if set(entry) != {"file", "new_file", "result_sha256"}:
                raise ReleaseError(f"{name}: new-file entry requires only file, new_file, and result_sha256")
            if target.exists():
                raise ReleaseError(f"{name}: new file already exists")
            if not isinstance(entry["new_file"], str):
                raise ReleaseError(f"{name}: new_file must contain UTF-8 source text")
            result = entry["new_file"].encode("utf-8")
            metadata = target.parent.stat()
            original = None
            patch_count = 0
            if digest(result) != expected_digest(entry["result_sha256"], name, "result_sha256"):
                raise ReleaseError(f"{name}: new-file result hash mismatch")
        else:
            if set(entry) != {"file", "sha256", "patches", "result_sha256"}:
                raise ReleaseError(f"{name}: existing files accept only sha256, old/new patches, and result_sha256")
            if not target.is_file():
                raise ReleaseError(f"{name}: existing source file is missing")
            metadata = target.stat()
            original = target.read_bytes()
            if digest(original) != expected_digest(entry["sha256"], name, "sha256"):
                raise ReleaseError(f"{name}: source hash changed")
            try:
                source = original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ReleaseError(f"{name}: source must be UTF-8") from exc
            patches = entry["patches"]
            if not isinstance(patches, list) or not patches:
                raise ReleaseError(f"{name}: patches must be a nonempty list")
            for index, patch in enumerate(patches, 1):
                if not isinstance(patch, dict) or set(patch) != {"old", "new"}:
                    raise ReleaseError(f"{name}: patch {index} must contain only old/new")
                old, new = patch["old"], patch["new"]
                if not isinstance(old, str) or not old or not isinstance(new, str):
                    raise ReleaseError(f"{name}: patch {index} requires nonempty old text and string new text")
                count = source.count(old)
                if count != 1:
                    raise ReleaseError(f"{name}: patch {index} old text matches {count} times, expected 1")
                source = source.replace(old, new, 1)
            result = source.encode("utf-8")
            if digest(result) != expected_digest(entry["result_sha256"], name, "result_sha256"):
                raise ReleaseError(f"{name}: reconstructed result hash mismatch")
            patch_count = len(patches)
        try:
            compile(result, str(target), "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            # Do not include a source line in error output.
            line = getattr(exc, "lineno", None)
            raise ReleaseError(f"{name}: Python compilation failed at line {line}") from exc
        plans.append(FilePlan(name, target, original, result, metadata, patch_count))
    return plans


def verify_unchanged(plan: FilePlan) -> None:
    directory = plan.path
    for _ in Path(plan.name).parts:
        directory = directory.parent
    target_path(directory, plan.name)
    if plan.original is None:
        if plan.path.exists():
            raise ReleaseError(f"{plan.name}: new file appeared during verification")
        return
    if not plan.path.is_file() or digest(plan.path.read_bytes()) != digest(plan.original):
        raise ReleaseError(f"{plan.name}: source changed during verification")
    current = plan.path.stat()
    if (current.st_uid, current.st_gid, stat.S_IMODE(current.st_mode)) != (plan.metadata.st_uid, plan.metadata.st_gid, stat.S_IMODE(plan.metadata.st_mode)):
        raise ReleaseError(f"{plan.name}: source ownership or mode changed")


def atomic_write(path: Path, data: bytes, metadata=None, *, mode: int = 0o600, replaced=None) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            if metadata is not None:
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
                    os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if replaced is not None:
            replaced()
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def sqlite_paths(values) -> list[Path]:
    paths = []
    for value in values:
        path = Path(value).resolve(strict=True)
        if not path.is_file():
            raise ReleaseError(f"SQLite source is not a file: {path}")
        # Do not open SQLite in dry-run: even a read-only WAL connection can
        # create shared-memory sidecars. Header inspection only reads bytes.
        with path.open("rb") as stream:
            header = stream.read(16)
        if header and header != b"SQLite format 3\x00":
            raise ReleaseError(f"SQLite source has an invalid header: {path}")
        if path not in paths:
            paths.append(path)
    return paths


def backup_sqlite(source: Path, destination: Path) -> None:
    deadline = time.monotonic() + 60
    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise ReleaseError(f"SQLite backup exceeded 60 seconds: {source.name}")
    # The backup API includes committed WAL data; copying just the main file
    # would not produce a consistent backup of a live database.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied, pages=256, progress=progress, sleep=0.05)
            if copied.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ReleaseError(f"SQLite backup integrity check failed: {source.name}")
    os.chmod(destination, 0o600)


@contextmanager
def optional_lock(filename: Optional[str], apply: bool):
    if filename is None:
        yield
        return
    import fcntl
    path = Path(filename)
    if path.is_symlink():
        raise ReleaseError("Lock file must not be a symlink")
    if not apply and not path.exists():
        # Dry-run never creates a lock file or its parent directory.
        yield
        return
    flags = (os.O_RDWR | os.O_CREAT) if apply else os.O_RDONLY
    flags |= os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReleaseError("Lock path must be a regular file")
        operation = fcntl.LOCK_EX if apply else fcntl.LOCK_SH
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReleaseError("Release lock is held; retry after the other operation finishes") from exc
        yield
    finally:
        os.close(descriptor)


def apply_release(plans: list[FilePlan], databases: list[Path], backup: Path, manifest_bytes: bytes, source_dir: Path):
    if backup.exists():
        if not backup.is_dir() or any(backup.iterdir()):
            raise ReleaseError("Backup directory must be absent or empty")
    else:
        backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup, 0o700)
    sources = backup / "sources"
    sources.mkdir(mode=0o700)
    database_dir = backup / "sqlite"
    if databases:
        database_dir.mkdir(mode=0o700)
    atomic_write(backup / "manifest.json", manifest_bytes)
    journal = {"status": "backing_up", "manifest_sha256": digest(manifest_bytes), "source_dir": str(source_dir), "files": [], "sqlite": [], "written": [], "rollback_errors": []}
    for plan in plans:
        journal["files"].append({"file": plan.name, "new_file": plan.original is None, "original_sha256": digest(plan.original) if plan.original is not None else None, "result_sha256": digest(plan.result), "uid": plan.metadata.st_uid, "gid": plan.metadata.st_gid, "mode": oct(stat.S_IMODE(plan.metadata.st_mode)) if plan.original is not None else "0o644"})
    def save_journal():
        atomic_write(backup / "journal.json", (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode())
    save_journal()
    attempted: list[FilePlan] = []
    try:
        for plan in plans:
            verify_unchanged(plan)
            if plan.original is not None:
                destination = sources / plan.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(destination, plan.original, plan.metadata, mode=stat.S_IMODE(plan.metadata.st_mode))
        for index, database in enumerate(databases, 1):
            destination = database_dir / f"{index:03d}-{database.name}"
            backup_sqlite(database, destination)
            journal["sqlite"].append({"source": str(database), "backup": str(destination)})
            save_journal()
        # All backups complete and every source remains verified before any
        # source is changed. Check again immediately before each replacement.
        for plan in plans:
            verify_unchanged(plan)
        journal["status"] = "applying"
        save_journal()
        for plan in plans:
            verify_unchanged(plan)
            # Track before replacement as well: Ctrl-C can arrive between the
            # successful os.replace syscall and its journal callback.
            attempted.append(plan)
            def record_write(current=plan):
                journal["written"].append(current.name)
            mode = stat.S_IMODE(plan.metadata.st_mode) if plan.original is not None else 0o644
            atomic_write(plan.path, plan.result, plan.metadata, mode=mode, replaced=record_write)
            save_journal()
        for plan in plans:
            if digest(plan.path.read_bytes()) != digest(plan.result):
                raise ReleaseError(f"{plan.name}: post-write result hash mismatch")
        journal["status"] = "applied"
        save_journal()
    except BaseException as exc:
        journal["status"] = "rolling_back"
        journal["error"] = str(exc)
        for plan in reversed(attempted):
            try:
                if plan.original is None:
                    plan.path.unlink(missing_ok=True)
                else:
                    if not plan.path.is_file() or digest(plan.path.read_bytes()) != digest(plan.original):
                        atomic_write(plan.path, plan.original, plan.metadata, mode=stat.S_IMODE(plan.metadata.st_mode))
                if plan.original is not None and digest(plan.path.read_bytes()) != digest(plan.original):
                    raise ReleaseError("Restored source hash mismatch")
            except BaseException as rollback_exc:
                journal["rollback_errors"].append({"file": plan.name, "error": str(rollback_exc)})
        journal["status"] = "rollback_failed" if journal["rollback_errors"] else "rolled_back"
        try:
            save_journal()
        except BaseException as journal_exc:
            raise ReleaseError(f"Release failed; journal could not be saved; backup retained at {backup}") from journal_exc
        if journal["rollback_errors"]:
            raise ReleaseError(f"Release failed and rollback needs manual recovery; see {backup / 'journal.json'}") from exc
        raise ReleaseError(f"Release failed; source changes rolled back; see {backup / 'journal.json'}") from exc
    return journal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--sqlite-path", action="append", default=[])
    parser.add_argument("--lock-file")
    parser.add_argument("--apply", action="store_true", help="Write verified patches; the default only validates")
    args = parser.parse_args()
    source_dir = args.source_dir.resolve(strict=True)
    if not source_dir.is_dir():
        raise ReleaseError("Source directory is not a directory")
    backup = args.backup_dir.resolve()
    if backup == source_dir or backup in source_dir.parents or source_dir in backup.parents:
        raise ReleaseError("Backup directory must be separate from the source directory")
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    with optional_lock(args.lock_file, args.apply):
        plans = prepare(manifest, source_dir)
        databases = sqlite_paths(args.sqlite_path)
        if args.apply:
            journal = apply_release(plans, databases, backup, manifest_bytes, source_dir)
            result = {"status": journal["status"], "backup_dir": str(backup)}
        else:
            result = {"status": "dry_run", "writes": 0}
        result.update({"files": len(plans), "new_files": sum(p.original is None for p in plans), "exact_blocks": sum(p.patch_count for p in plans), "sqlite_backups": len(databases)})
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ReleaseError, OSError, ValueError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
