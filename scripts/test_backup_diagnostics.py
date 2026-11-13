"""同步点控制的真实材料变化；不替换安全检查的结果。"""

from __future__ import annotations
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Literal
import pytest
from test_backup_fixture import Fixture, git, setup, snapshot
from scripts import backup, backup_files, backup_source
from scripts.backup_diagnostics import FailureContext, UNKNOWN_CONTEXT


def invoke(sample: Fixture, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backup.py",
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
    )
    previous = os.umask(0o077)
    try:
        return backup.main()
    finally:
        os.umask(previous)


@pytest.mark.parametrize("role", ["运行配置", "登录密码", "外部规则"])
@pytest.mark.parametrize("stage", ["运行材料读取", "运行材料复查"])
def test_runtime_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], role: str, stage: str
) -> None:
    sample = setup(tmp_path)
    external = tmp_path.resolve() / "private-external-rules.json"
    _ = external.write_text("private-source-secret-test-password-never-log")
    _ = (sample.root / "backend/.runtime.json").write_text(json.dumps({"STOCKMOCK_INSTRUMENTS_FILE": str(external)}))
    password = {
        "运行配置": sample.root / "backend/.runtime.json",
        "登录密码": sample.root / "backend/.local-password",
        "外部规则": external,
    }[role]
    reads: list[bool] = []
    original = Path.read_bytes
    observed: list[bool] = []

    def change_after_read(path: Path) -> bytes:
        data = original(path)
        if path == password:
            reads.append(True)
            if len(reads) == (1 if stage == "运行材料读取" else 2):
                _ = path.write_bytes(data + b" ")
                observed.append(True)
        return data

    monkeypatch.setattr(Path, "read_bytes", change_after_read)
    assert invoke(sample, monkeypatch) == 1
    assert observed == [True]
    output = capsys.readouterr().err
    record = json.loads((sample.destination / "failure.json").read_text())
    assert record["code"] == "source_changed"
    assert record["context"] == {
        "stage": stage,
        "material": role,
        "check": "读取前后状态变化",
    }
    assert record["message"] in output
    assert "尚不存在" in output and "父目录" in output and "不能用于恢复" in output
    assert str(sample.root) not in output + json.dumps(record, ensure_ascii=False)
    assert "secret-test-password-never-log" not in output + json.dumps(record)
    assert str(external) not in output + json.dumps(record)
    assert "源码或归档配套检查失败" not in output
    assert sample.inspect().returncode == 1


@pytest.mark.parametrize("phase", ["initial_git", "final_git", "final_commit", "final_lock"])
def test_source_change_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: Literal["initial_git", "final_git", "final_commit", "final_lock"],
) -> None:
    sample = setup(tmp_path)
    assert sample.create().returncode == 0
    saved = snapshot(sample.destination)
    failed = replace(sample, destination=sample.destination.with_name("failed"))
    original_git = backup_source.git
    original_database = backup.database
    original_tree = backup.tree
    lock = sample.root / "backend/uv.lock"
    status_calls: list[bool] = []
    reference_calls: list[bool] = []
    changed: list[bool] = []

    def change() -> None:
        _ = lock.write_text("private-source-secret-test-password-never-log")
        changed.append(True)

    def after_status(root: Path, arguments: list[str]) -> bytes:
        data = original_git(root, arguments)
        if arguments[0] == "status":
            status_calls.append(True)
            if (phase == "initial_git" and len(status_calls) == 1) or (phase == "final_git" and len(status_calls) == 2):
                change()
        return data

    def after_database(path: Path) -> dict[str, int]:
        data = original_database(path)
        if phase == "final_commit":
            change()
            _ = git(sample.root, "add", "backend/uv.lock")
            _ = git(
                sample.root,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "同步点改变提交",
            )
        return data

    def after_reference(
        path: Path, *, private: bool = False, context: FailureContext = UNKNOWN_CONTEXT
    ) -> dict[str, bytes]:
        data = original_tree(path, private=private, context=context)
        if path == sample.reference:
            reference_calls.append(True)
            if phase == "final_lock" and len(reference_calls) == 2:
                change()
        return data

    monkeypatch.setattr(backup_source, "git", after_status)
    monkeypatch.setattr(backup, "database", after_database)
    monkeypatch.setattr(backup, "tree", after_reference)
    assert invoke(failed, monkeypatch) == 1
    assert changed == [True]
    record = json.loads((failed.destination / "failure.json").read_text())
    expected = {
        "initial_git": ("源码检查", "源码", "与Git内容不一致"),
        "final_git": ("源码复查", "源码", "与Git内容不一致"),
        "final_commit": ("源码复查", "源码", "提交前后不一致"),
        "final_lock": ("依赖材料复查", "依赖清单与锁文件", "前后内容不一致"),
    }[phase]
    assert record["code"] == "source_changed"
    assert record["context"] == dict(zip(("stage", "material", "check"), expected, strict=True))
    output = capsys.readouterr().err
    assert record["message"] in output
    assert str(sample.root) not in output + json.dumps(record)
    assert "secret-test-password-never-log" not in output + json.dumps(record)
    assert failed.inspect().returncode == 1
    assert snapshot(sample.destination) == saved
    assert sample.inspect().returncode == 0
    assert lock.read_text() == "private-source-secret-test-password-never-log"


def test_failure_record_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = setup(tmp_path)
    original = backup_files.read

    def obstruct_record(path: Path, *, private: bool = False, context: FailureContext = UNKNOWN_CONTEXT) -> bytes:
        data = original(path, private=private, context=context)
        if path.name == ".runtime.json":
            # 已存在目录让 O_EXCL 写失败，跨平台确定性触发真实文件系统错误。
            (sample.destination / "failure.json").mkdir()
            _ = path.write_text("{} ")
        return data

    monkeypatch.setattr(backup_files, "read", obstruct_record)
    # runtime 持有导入的函数，显式控制同一真实读取同步点。
    from scripts import backup_runtime

    monkeypatch.setattr(backup_runtime, "read", obstruct_record)
    _ = (sample.root / "backend/.local-password").write_text("short")
    assert invoke(sample, monkeypatch) == 1
    output = capsys.readouterr().err
    assert "[failure_record]" in output and "无法写入失败记录" in output
    assert "不能用于恢复" in output and "尚不存在" in output
    assert sample.inspect().returncode == 1


def test_raw_exception_is_not_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = setup(tmp_path)

    def fail_runtime(_root: Path) -> dict[str, bytes]:
        raise PermissionError("private-source-secret-test-password-never-log")

    monkeypatch.setattr(backup, "runtime", fail_runtime)
    assert invoke(sample, monkeypatch) == 1
    record = json.loads((sample.destination / "failure.json").read_text())
    output = capsys.readouterr().err
    assert record["message"] in output
    assert record["context"]["check"] == "未知"
    assert "secret-test-password-never-log" not in output + json.dumps(record)
