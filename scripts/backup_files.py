"""备份的路径、权限与稳定文件读取。"""

from __future__ import annotations
import os
import stat
from pathlib import Path
from typing import Final


class BackupError(RuntimeError):
    """仅包含固定安全错误码。"""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise BackupError(code)


def absolute(path: Path) -> Path:
    require(".." not in path.parts, "path_parent_component")
    return path.absolute()


def ancestors(path: Path) -> None:
    for item in (path, *path.parents):
        require(not item.is_symlink(), "symbolic_link")


def directory(path: Path, *, private: bool = False) -> None:
    ancestors(path)
    info = path.stat()
    require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid(),
        "directory_owner_or_type",
    )
    if private:
        require(stat.S_IMODE(info.st_mode) == 0o700, "directory_permissions")


def regular(path: Path, *, private: bool = False) -> os.stat_result:
    ancestors(path)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "file_owner_or_type")
    if private:
        require(stat.S_IMODE(info.st_mode) == 0o600, "file_permissions")
    return info


def stamp(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read(path: Path, *, private: bool = False) -> bytes:
    before = regular(path, private=private)
    result = path.read_bytes()
    require(stamp(before) == stamp(regular(path, private=private)), "source_changed")
    return result


def write(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        _ = stream.write(data)


def tree(path: Path, *, private: bool = False) -> dict[str, bytes]:
    """读取完整树；不追随目录或文件链接。"""
    directory(path, private=private)
    result: dict[str, bytes] = {}
    for item in sorted(path.rglob("*")):
        ancestors(item)
        if item.is_dir():
            directory(item, private=private)
        else:
            result[item.relative_to(path).as_posix()] = read(item, private=private)
    return result


def copy_tree(files: dict[str, bytes], destination: Path) -> None:
    destination.mkdir(mode=0o700)
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write(target, data)


REQUIRED: Final = (
    "backend/app/main.py",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "frontend/package.json",
    "frontend/bun.lock",
    "scripts/run-service.py",
)
