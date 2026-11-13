#!/usr/bin/env python3
"""手动成套备份；使用 backend/.venv/bin/python scripts/backup.py --help。"""

from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "scripts"

from pydantic import ValidationError
from .backup_files import (
    BackupError,
    absolute,
    ancestors,
    copy_tree,
    directory,
    read,
    require,
    tree,
    write,
)
from .backup_package import Manifest, inspect, restore_paths
from .backup_runtime import database, online, runtime
from .backup_source import archive, archive_version, clean_source, git, paired_frontend


class Arguments(argparse.Namespace):
    action: str = "inspect"
    root: Path = Path()
    destination: Path = Path()
    static_reference: Path = Path()
    static_source_commit: str = ""
    backup: Path = Path()


def create(args: Arguments) -> tuple[Path, Manifest]:
    root = absolute(args.root)
    destination = absolute(args.destination)
    reference = absolute(args.static_reference)
    directory(root)
    directory(destination.parent, private=True)
    ancestors(destination)
    require(not destination.exists(), "destination_exists")
    require(
        not destination.is_relative_to(root / "frontend/dist") and not destination.is_relative_to(reference),
        "destination_source_overlap",
    )
    if destination.is_relative_to(root):
        require(destination.is_relative_to(root / "work"), "destination_project_location")
        _ = git(root, ["check-ignore", "--quiet", str(destination)])
    destination.mkdir(mode=0o700)
    try:
        head = clean_source(root)
        paired_frontend(root, args.static_source_commit)
        source_dist = root / "frontend/dist"
        require(source_dist != reference, "reference_same_directory")
        static = tree(source_dist)
        require(
            bool(static.get("index.html")) and static == tree(reference),
            "static_reference_mismatch",
        )
        materials = runtime(root)
        source_files = {
            name: read(root / name)
            for name in (
                "backend/pyproject.toml",
                "backend/uv.lock",
                "frontend/package.json",
                "frontend/bun.lock",
            )
        }
        copy_tree(materials, destination / "runtime")
        archive(root, destination / "source.tar", head)
        version = archive_version(destination / "source.tar", head)
        copy_tree(static, destination / "frontend-dist")
        online(root / "backend/data/paper.sqlite3", destination / "paper.sqlite3")
        tables = database(destination / "paper.sqlite3")
        require(head == clean_source(root), "source_changed")
        require(materials == runtime(root), "runtime_changed")
        require(static == tree(source_dist) == tree(reference), "static_changed")
        require(
            all(data == read(root / name) for name, data in source_files.items()),
            "source_changed",
        )
        files = tree(destination, private=True)
        manifest = Manifest(
            status="complete",
            static_verification="independent_reference_bytes_and_frontend_build_inputs",
            created_at=datetime.now(timezone.utc),
            source_commit=head,
            static_source_commit=args.static_source_commit,
            version=version,
            files={name: len(data) for name, data in files.items()},
            tables=tables,
            restore_paths=restore_paths("instruments.json" in materials),
        )
        write(destination / "result.json", manifest.model_dump_json(indent=2).encode())
    except (
        BackupError,
        OSError,
        ValueError,
        sqlite3.Error,
        subprocess.SubprocessError,
        tarfile.TarError,
        KeyError,
    ) as error:
        code = safe_code(error)
        try:
            write(
                destination / "failure.json",
                json.dumps({"status": "failed", "code": code}).encode(),
            )
        except OSError:
            print("[failure_record] 无法写入失败记录；部分包仍不可用。", file=sys.stderr)
        raise
    return destination, manifest


def safe_code(error: BaseException) -> str:
    """在唯一命令行边界分类，绝不回显第三方错误正文。"""
    match error:
        case BackupError():
            return str(error)
        case FileNotFoundError():
            return "material_missing"
        case PermissionError():
            return "material_permissions"
        case subprocess.TimeoutExpired():
            return "source_command_timeout"
        case subprocess.SubprocessError():
            return "source_command_failed"
        case sqlite3.Error():
            return "database_operation_failed"
        case ValueError() | KeyError() | tarfile.TarError():
            return "material_format_invalid"
        case _:
            return "material_io_failed"


def diagnosis(code: str) -> str:
    if code.startswith(("database",)):
        return "数据库读取、完整性或锁等待检查失败；请检查源库及占用。"
    if code.startswith(("static", "reference")):
        return "前端参考包或构建来源不匹配；请重新核对独立参考与来源提交。"
    if code.startswith(("source", "dirty", "untracked", "archive", "version")):
        return "源码或归档配套检查失败；请核对完整提交、干净工作树及版本。"
    if code.startswith(("destination", "directory", "symbolic", "file_", "path_", "material_permissions")):
        return "路径或权限不安全；请使用本人0700私有父目录下的全新目标，项目内只用work目录。"
    if code.startswith(("config", "password", "rules", "runtime", "material_missing")):
        return "必要运行材料缺失、变化或不合法；请检查配置、密码、外部规则及源库。"
    return "材料不完整或读写失败；请检查所选包、文件格式与磁盘可用空间。"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create_parser = actions.add_parser("create", help="生成新的私有成套包")
    for flag in ("root", "destination", "static-reference"):
        _ = create_parser.add_argument("--" + flag, type=Path, required=True)
    _ = create_parser.add_argument("--static-source-commit", required=True)
    inspect_parser = actions.add_parser("inspect", help="只读检查材料，不执行恢复")
    _ = inspect_parser.add_argument("--backup", type=Path, required=True)
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    _ = os.umask(0o077)
    try:
        match args.action:
            case "create":
                destination, manifest = create(args)
                print(
                    f"成套备份完成：{destination}；时间 {manifest.created_at.isoformat()}；"
                    + f"版本 {manifest.version}；源码 {manifest.source_commit}；"
                    + f"静态来源 {manifest.static_source_commit}；尚未执行恢复验收。"
                )
            case "inspect":
                manifest = inspect(absolute(args.backup))
                print(
                    f"检查位置：{args.backup}；时间 {manifest.created_at.isoformat()}；"
                    + f"版本 {manifest.version}；源码 {manifest.source_commit}；"
                    + f"静态来源 {manifest.static_source_commit}。"
                )
                print("材料检查通过；不代表恢复验收通过，也不提供任意内容篡改检测。")
            case _:
                raise BackupError("action_invalid")
        return 0
    except (
        BackupError,
        OSError,
        ValueError,
        sqlite3.Error,
        subprocess.SubprocessError,
        tarfile.TarError,
        KeyError,
        ValidationError,
    ) as error:
        code = safe_code(error)
        print(f"[{code}] {diagnosis(code)} 未确认成套可用。", file=sys.stderr)
        if args.action == "create":
            print(f"目标（如已创建则保留部分包）：{args.destination}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
