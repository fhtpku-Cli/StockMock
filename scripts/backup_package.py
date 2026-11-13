"""成套备份的成功清单与只读材料检查。"""

from __future__ import annotations
from typing import ClassVar
from pathlib import Path, PurePosixPath
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from .backup_files import require, tree
from .backup_runtime import ALLOWED, database
from .backup_source import archive_version


class Manifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid", frozen=True)
    status: Literal["complete"]
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    static_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    version: str = Field(min_length=1)
    created_at: datetime
    static_verification: Literal["independent_reference_bytes_and_frontend_build_inputs"]
    files: dict[str, int]
    tables: dict[str, int]
    restore_paths: dict[str, str]


def restore_paths(has_rules: bool) -> dict[str, str]:
    result = {
        "paper.sqlite3": "backend/data/paper.sqlite3",
        "runtime/password": "backend/.local-password",
        "runtime/config.json": "backend/.runtime.json",
        "frontend-dist": "frontend/dist",
        "source.tar": ".",
    }
    if has_rules:
        result["runtime/instruments.json"] = "backend/instruments.json"
    return result


def inspect(package: Path) -> Manifest:
    """只验证材料，不读取原路径，也不执行恢复或应用初始化。"""
    files = tree(package, private=True)
    require("result.json" in files and "failure.json" not in files, "incomplete_package")
    manifest = Manifest.model_validate_json(files.pop("result.json"))
    require(
        {name: len(data) for name, data in files.items()} == manifest.files,
        "package_file_set",
    )
    for name, size in manifest.files.items():
        path = PurePosixPath(name)
        require(
            not path.is_absolute() and ".." not in path.parts and size >= 0,
            "manifest_path",
        )
    required = {
        "paper.sqlite3",
        "source.tar",
        "runtime/config.json",
        "runtime/password",
        "frontend-dist/index.html",
    }
    require(required <= files.keys(), "package_required")
    config = TypeAdapter(dict[str, str]).validate_json(files["runtime/config.json"], strict=True)
    require(config.keys() <= ALLOWED, "config_fields")
    require(len(files["runtime/password"].decode().strip()) >= 12, "password_length")
    has_rules = "STOCKMOCK_INSTRUMENTS_FILE" in config
    require(has_rules == ("runtime/instruments.json" in files), "rules_mapping")
    require(manifest.restore_paths == restore_paths(has_rules), "restore_mapping")
    require(
        archive_version(package / "source.tar", manifest.source_commit) == manifest.version,
        "version_mismatch",
    )
    require(database(package / "paper.sqlite3") == manifest.tables, "database_tables")
    return manifest
