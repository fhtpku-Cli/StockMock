"""核对源码、静态包配套关系与安全归档。"""

from __future__ import annotations
from typing import ClassVar, Literal
from .backup_diagnostics import FailureContext
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path, PurePosixPath
from pydantic import BaseModel, ConfigDict
from .backup_files import REQUIRED, read, regular, require


class Versions(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="ignore", frozen=True)
    version: str


class Project(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="ignore", frozen=True)
    project: Versions


def git(root: Path, arguments: list[str]) -> bytes:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *arguments],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout


def commit(root: Path, revision: str) -> str:
    value = git(root, ["rev-parse", "--verify", f"{revision}^{{commit}}"]).decode().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", value) is not None, "source_commit")
    return value


def clean_source(root: Path, *, stage: Literal["源码检查", "源码复查"] = "源码检查") -> str:
    require(not git(root, ["status", "--porcelain", "--untracked-files=no"]), "dirty_source")
    untracked = (
        git(root, ["ls-files", "--others", "-z", "--", "backend/app", "scripts", "frontend/src"]).decode().split("\0")
    )
    source_extensions = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".html", ".vue", ".svelte"}
    require(not any(Path(name).suffix in source_extensions for name in untracked if name), "untracked_source")
    head = commit(root, "HEAD")
    for name in REQUIRED:
        require(
            read(root / name, context=FailureContext(stage, "源码")) == git(root, ["show", f"{head}:{name}"]),
            "source_changed",
            FailureContext(stage, "源码", "与Git内容不一致"),
        )
    return head


def paired_frontend(root: Path, revision: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "static_commit")
    require(commit(root, revision) == revision, "static_commit")
    # 文档不参与前端构建；其余跟踪文件（含锁文件、配置和资源）全部比较。
    changed = git(root, ["diff", "--name-only", "-z", revision, "HEAD", "--", "frontend"]).decode().split("\0")
    require(
        all(not name or name.endswith(".md") for name in changed),
        "static_source_mismatch",
    )


def archive(root: Path, destination: Path, head: str) -> None:
    with destination.open("xb") as stream:
        _ = subprocess.run(
            ["git", "-C", str(root), "archive", "--format=tar", head],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=True,
            timeout=600,
        )


def archive_version(path: Path, expected_commit: str) -> str:
    """不解压归档，拒绝链接、重复和越界成员。"""
    _ = regular(path, private=True)
    with tarfile.open(path, "r:") as package:
        require(
            package.pax_headers.get("comment") == expected_commit,
            "archive_commit_mismatch",
        )
        names: set[str] = set()
        for member in package.getmembers():
            name = PurePosixPath(member.name)
            require(
                not name.is_absolute()
                and ".." not in name.parts
                and member.name not in names
                and (member.isfile() or member.isdir()),
                "archive_member",
            )
            names.add(member.name)
        require(set(REQUIRED) <= names, "archive_required")
        backend = package.extractfile("backend/pyproject.toml")
        frontend = package.extractfile("frontend/package.json")
        require(backend is not None and frontend is not None, "archive_required")
        assert backend is not None and frontend is not None
        with backend, frontend:
            version = Project.model_validate(tomllib.loads(backend.read().decode())).project.version
            front_version = Versions.model_validate_json(frontend.read()).version
        require(version == front_version and bool(version), "version_mismatch")
        return version
