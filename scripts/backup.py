#!/usr/bin/env python3
"""手动成套备份；使用 backend/.venv/bin/python scripts/backup.py --help。"""

from __future__ import annotations
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import NoReturn, override

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
from .backup_diagnostics import FailureContext, failure_message, inspect_failure_message, safe_code
from .backup_package import Manifest, inspect, restore_paths
from .backup_runtime import database, online, runtime
from .backup_source import archive, archive_version, clean_source, git, paired_frontend


class BackupArgumentParser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(
            2,
            "命令填写不完整，或有不认识的选项。没有开始备份或恢复操作。"
            + "请按操作说明重新填写；也可以使用 --help 查看填写方式。\n",
        )


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
    context = FailureContext("源码检查", "源码")
    try:
        head = clean_source(root)
        paired_frontend(root, args.static_source_commit)
        source_dist = root / "frontend/dist"
        require(source_dist != reference, "reference_same_directory")
        context = FailureContext("静态材料读取", "前端静态材料")
        static = tree(source_dist, context=context)
        require(
            bool(static.get("index.html")) and static == tree(reference, context=context),
            "static_reference_mismatch",
        )
        context = FailureContext("运行材料读取", "运行材料")
        materials = runtime(root)
        context = FailureContext("依赖材料读取", "依赖清单与锁文件")
        source_files = {
            name: read(root / name, context=context)
            for name in (
                "backend/pyproject.toml",
                "backend/uv.lock",
                "frontend/package.json",
                "frontend/bun.lock",
            )
        }
        context = FailureContext("材料复制", "运行材料")
        copy_tree(materials, destination / "runtime")
        context = FailureContext("源码归档", "源码")
        archive(root, destination / "source.tar", head)
        version = archive_version(destination / "source.tar", head)
        context = FailureContext("材料复制", "前端静态材料")
        copy_tree(static, destination / "frontend-dist")
        context = FailureContext("数据库备份", "数据库")
        online(root / "backend/data/paper.sqlite3", destination / "paper.sqlite3")
        tables = database(destination / "paper.sqlite3")
        context = FailureContext("源码复查", "源码")
        require(
            head == clean_source(root, stage="源码复查"),
            "source_changed",
            FailureContext("源码复查", "源码", "提交前后不一致"),
        )
        context = FailureContext("运行材料复查", "运行材料")
        require(
            materials == runtime(root, stage="运行材料复查"),
            "runtime_changed",
            FailureContext("运行材料复查", "运行材料", "前后内容不一致"),
        )
        context = FailureContext("静态材料复查", "前端静态材料")
        require(
            static == tree(source_dist, context=context) == tree(reference, context=context),
            "static_changed",
            replace(context, check="前后内容不一致"),
        )
        context = FailureContext("依赖材料复查", "依赖清单与锁文件")
        require(
            all(data == read(root / name, context=context) for name, data in source_files.items()),
            "source_changed",
            replace(context, check="前后内容不一致"),
        )
        context = FailureContext("备份包检查", "备份包")
        files = tree(destination, private=True, context=context)
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
        if isinstance(error, BackupError) and error.context.stage != "未知":
            context = error.context
        failure = BackupError(safe_code(error), context)
        try:
            write(
                destination / "failure.json",
                json.dumps(
                    {
                        "status": "failed",
                        "code": safe_code(failure),
                        "context": asdict(context),
                        "message": failure_message(failure),
                    },
                    ensure_ascii=False,
                ).encode(),
            )
        except OSError:
            print(
                "失败原因也没能保存到文件中。请保留当前终端文字，交给维护者查看；本次备份不能用于恢复。",
                file=sys.stderr,
            )
        raise failure from error
    return destination, manifest


def main() -> int:
    parser = BackupArgumentParser(description=__doc__)
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
        if args.action == "create":
            failure = error if isinstance(error, BackupError) else BackupError(code)
            print(failure_message(failure), file=sys.stderr)
        else:
            print(inspect_failure_message(code), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
