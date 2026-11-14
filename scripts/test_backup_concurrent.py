"""并发和操作系统真实失败路径。"""

from __future__ import annotations
import json
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from test_backup_fixture import CLI, assert_failure, setup


def test_destination_never_enters_served_or_reference_tree(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    for parent in (sample.root / "frontend/dist", sample.reference):
        parent.chmod(0o700)
        target = parent / "backup"
        result = sample.create("--destination", str(target))
        assert_failure(result)
        assert not target.exists()
    (sample.root / "work").mkdir(mode=0o700)
    result = sample.create(
        "--static-reference", str(sample.root / "work"), "--destination", str(sample.root / "work/backup")
    )
    assert_failure(result)
    assert not (sample.root / "work/backup").exists()
    assert sample.create("--destination", str(sample.root / "work/good")).returncode == 0


def test_ignored_untracked_source_rejected(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    _ = (sample.root / ".git/info/exclude").write_text("hidden.py\n")
    _ = (sample.root / "backend/app/hidden.py").write_text("pass")
    result = sample.create()
    assert_failure(result)
    record = json.loads((sample.destination / "failure.json").read_text())
    assert record["code"] == "untracked_source"
    assert record["message"] in result.stderr
    assert "尚未确认版本" in result.stderr


def test_concurrent_runtime_change_rejected(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    with closing(sqlite3.connect(sample.root / "backend/data/paper.sqlite3")) as connection:
        _ = connection.execute("BEGIN EXCLUSIVE")
        with subprocess.Popen(
            [
                sys.executable,
                str(CLI),
                "create",
                "--root",
                str(sample.root),
                "--destination",
                str(sample.destination),
                "--static-reference",
                str(sample.reference),
                "--static-source-commit",
                sample.commit,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            deadline = time.monotonic() + 5
            while not (sample.destination / "paper.sqlite3").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert (sample.destination / "paper.sqlite3").exists()
            _ = (sample.root / "backend/.runtime.json").write_text('{"PAPER_ALLOWED_HOSTS":"changed.invalid"}')
            connection.rollback()
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode != 0
            record = json.loads((sample.destination / "failure.json").read_text())
            assert record["code"] == "runtime_changed"
            assert record["message"] in stderr
            assert "文件发生了变化" in stderr
            assert "changed.invalid" not in stdout + stderr
    assert not (sample.destination / "result.json").exists()


def test_live_wal_writer_remains_active(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    stop = threading.Event()
    ready = threading.Event()
    counts: list[int] = []

    def writer() -> None:
        with closing(sqlite3.connect(sample.root / "backend/data/paper.sqlite3")) as connection:
            assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
            for number in range(2, 1000):
                if stop.is_set():
                    break
                _ = connection.execute("INSERT INTO entries VALUES(?, ?)", (number, str(number)))
                connection.commit()
                counts.append(number)
                ready.set()
                time.sleep(0.005)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert ready.wait(5)
        before = len(counts)
        result = sample.create()
        assert result.returncode == 0, result.stderr
        assert len(counts) > before
        with closing(sqlite3.connect(sample.destination / "paper.sqlite3")) as connection:
            rows = connection.execute("SELECT id FROM entries ORDER BY id").fetchall()
            assert len(rows) >= 2
            assert rows == [(i,) for i in range(1, len(rows) + 1)]
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_real_partial_copy_failure(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    _ = (sample.root / "backend/.runtime.json").write_text(json.dumps({"PAPER_ALLOWED_HOSTS": "x" * 10000}))
    wrapper = (
        "import resource,runpy,sys; resource.setrlimit(resource.RLIMIT_FSIZE,(1024,1024)); "
        + "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            wrapper,
            str(CLI),
            "create",
            "--root",
            str(sample.root),
            "--destination",
            str(sample.destination),
            "--static-reference",
            str(sample.reference),
            "--static-source-commit",
            sample.commit,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert_failure(result)
    assert (sample.destination / "runtime/config.json").stat().st_size == 1024
    assert (sample.destination / "failure.json").exists()
    assert not (sample.destination / "result.json").exists()
