"""成套备份与失败边界的真实命令行验证。"""

from __future__ import annotations
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
import pytest
from test_backup_fixture import assert_failure, git, setup, snapshot


def test_create_inspect_and_duplicate(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    source = snapshot(sample.root)
    result = sample.create()
    assert result.returncode == 0, result.stderr
    assert source == snapshot(sample.root)
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in sample.destination.rglob("*") if p.is_dir())
    before = snapshot(sample.destination)
    assert sample.inspect().returncode == 0
    assert snapshot(sample.destination) == before
    assert_failure(sample.create())
    assert snapshot(sample.destination) == before
    assert all(mode & 0o777 == 0o600 for _, _, mode in before.values())
    manifest = json.loads((sample.destination / "result.json").read_text())
    assert manifest["tables"] == {"entries": 1}
    assert manifest["source_commit"] == sample.commit


@pytest.mark.parametrize(
    "name",
    [
        "backend/data/paper.sqlite3",
        "backend/.local-password",
        "backend/.runtime.json",
        "backend/uv.lock",
    ],
)
def test_missing_material(tmp_path: Path, name: str) -> None:
    sample = setup(tmp_path)
    (sample.root / name).unlink()
    assert_failure(sample.create())
    assert not (sample.root / name).exists()
    assert not (sample.destination / "result.json").exists()


@pytest.mark.parametrize(
    "case",
    [
        "dirty",
        "untracked",
        "static",
        "same_reference",
        "bad_commit",
        "version",
        "config",
        "password",
        "rules",
        "permissions",
        "parent_permissions",
        "unignored",
        "symlink",
        "ancestor",
        "reference_link",
    ],
)
def test_rejections(tmp_path: Path, case: str) -> None:
    sample = setup(tmp_path)
    extra: list[str] = []
    match case:
        case "dirty":
            _ = (sample.root / "backend/app/main.py").write_text("changed")
        case "untracked":
            _ = (sample.root / "scripts/extra.py").write_text("pass")
        case "static":
            _ = (sample.reference / "index.html").write_text("mismatch")
        case "same_reference":
            extra = ["--static-reference", str(sample.root / "frontend/dist")]
        case "bad_commit":
            extra = ["--static-source-commit", "0" * 40]
        case "version":
            _ = (sample.root / "frontend/package.json").write_text('{"version":"different"}')
            _ = git(sample.root, "add", ".")
            _ = git(
                sample.root,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "版本不同",
            )
            extra = ["--static-source-commit", git(sample.root, "rev-parse", "HEAD")]
        case "config":
            _ = (sample.root / "backend/.runtime.json").write_text('{"BAD":"secret-test-password-never-log"}')
        case "password":
            _ = (sample.root / "backend/.local-password").write_text("short")
        case "rules":
            _ = (sample.root / "backend/.runtime.json").write_text('{"STOCKMOCK_INSTRUMENTS_FILE":"missing"}')
        case "permissions":
            (sample.root / "backend/.local-password").chmod(0o644)
        case "parent_permissions":
            sample.destination.parent.chmod(0o500)
        case "unignored":
            extra = ["--destination", str(sample.root / "backup")]
            sample.root.chmod(0o700)
        case "symlink":
            original = sample.root / "backend/.local-password"
            original.rename(original.with_suffix(".original"))
            original.symlink_to(original.with_suffix(".original"))
        case "ancestor":
            link = tmp_path.resolve() / "linked"
            link.symlink_to(sample.destination.parent, target_is_directory=True)
            extra = ["--destination", str(link / "backup")]
        case "reference_link":
            link = sample.reference / "extra"
            link.symlink_to(sample.root / "backend/.local-password")
    assert_failure(sample.create(*extra))
    assert not (sample.destination / "result.json").exists()


def test_wal_live_connection(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    with closing(sqlite3.connect(sample.root / "backend/data/paper.sqlite3")) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        _ = connection.execute("INSERT INTO entries VALUES(2, '200.02')")
        connection.commit()
        assert (sample.root / "backend/data/paper.sqlite3-wal").stat().st_size > 0
        assert sample.create().returncode == 0
        with closing(sqlite3.connect(sample.destination / "paper.sqlite3")) as restored:
            assert restored.execute("SELECT amount FROM entries ORDER BY id").fetchall() == [("100.01",), ("200.02",)]


def test_locked_database_deadline_and_partial_package(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    with closing(sqlite3.connect(sample.root / "backend/data/paper.sqlite3")) as connection:
        _ = connection.execute("BEGIN EXCLUSIVE")
        start = time.monotonic()
        result = sample.create()
        elapsed = time.monotonic() - start
        assert_failure(result)
        assert 5 <= elapsed < 10
    assert (sample.destination / "failure.json").exists()
    assert (sample.destination / "runtime/password").exists()
    assert not (sample.destination / "result.json").exists()
    assert_failure(sample.inspect())
