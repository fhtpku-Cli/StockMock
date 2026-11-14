"""真实命令行的白话拒绝与参数保密行为。"""

from __future__ import annotations
import json
import sys
from dataclasses import replace
from pathlib import Path
import pytest
from test_backup_fixture import CLI, assert_failure, command, setup, snapshot


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["create"],
        ["secret-test-password-never-log"],
        ["inspect", "--backup", "unused", "--unknown", "secret-test-password-never-log"],
        ["create", "--root", "secret-test-password-never-log"],
    ],
)
def test_invalid_arguments_are_private(tmp_path: Path, arguments: list[str]) -> None:
    before = snapshot(tmp_path)
    result = command([sys.executable, str(CLI), *arguments], tmp_path)
    assert_failure(result)
    assert result.returncode == 2
    assert "命令填写不完整" in result.stderr
    assert "--help" in result.stderr
    for internal in ("ArgumentError", "error:", "usage:", "unused"):
        assert internal not in result.stderr
    assert snapshot(tmp_path) == before
    assert list(tmp_path.iterdir()) == []


def test_existing_backup_is_preserved_and_described_accurately(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    assert sample.create().returncode == 0
    before = snapshot(sample.destination)
    result = sample.create()
    assert_failure(result)
    assert "已有同名" in result.stderr and "没有覆盖" in result.stderr
    assert "不能用于恢复" not in result.stderr
    assert "尚未使用" in result.stderr
    assert snapshot(sample.destination) == before
    assert sample.inspect().returncode == 0


def test_version_mismatch_and_failed_package_then_manual_retry(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    source = sample.root / "backend/app/main.py"
    original = source.read_bytes()
    _ = source.write_text("secret-test-password-never-log")
    result = sample.create()
    assert_failure(result)
    record = json.loads((sample.destination / "failure.json").read_text())
    assert record["code"] == "dirty_source"
    assert record["context"] == {"stage": "源码检查", "material": "源码", "check": "未知"}
    assert record["message"] in result.stderr
    assert "与已确认的版本不一致" in result.stderr
    before = snapshot(sample.destination)
    result = sample.inspect()
    assert_failure(result)
    assert "暂时不要用它恢复" in result.stderr and "保留原件" in result.stderr
    assert "损坏" not in result.stderr and "这次备份未完成" not in result.stderr
    assert snapshot(sample.destination) == before
    _ = source.write_bytes(original)
    retry = replace(sample, destination=sample.destination.with_name("new-backup"))
    assert retry.create().returncode == 0
    assert retry.inspect().returncode == 0
    assert snapshot(sample.destination) == before


def test_help_remains_available(tmp_path: Path) -> None:
    result = command([sys.executable, str(CLI), "--help"], tmp_path)
    assert result.returncode == 0
    assert "create" in result.stdout and "inspect" in result.stdout
    assert result.stderr == ""
