"""真实 CLI 测试的隔离 Git 和 SQLite 材料。"""

from __future__ import annotations
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

CLI = Path(__file__).resolve().with_name("backup.py")


def command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)


def git(root: Path, *args: str) -> str:
    result = command(["git", *args], root)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@dataclass(frozen=True, slots=True)
class Fixture:
    root: Path
    reference: Path
    destination: Path
    commit: str

    def create(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return command(
            [
                sys.executable,
                str(CLI),
                "create",
                "--root",
                str(self.root),
                "--destination",
                str(self.destination),
                "--static-reference",
                str(self.reference),
                "--static-source-commit",
                self.commit,
                *extra,
            ],
            self.root,
        )

    def inspect(self) -> subprocess.CompletedProcess[str]:
        return command(
            [sys.executable, str(CLI), "inspect", "--backup", str(self.destination)],
            self.root,
        )


def setup(tmp: Path) -> Fixture:
    # resolve 只用于测试 tmpdir：macOS 的 /var 是系统链接，工具应拒绝它。
    tmp = tmp.resolve()
    root = tmp / "source"
    root.mkdir()
    files = {
        "backend/app/main.py": "# test application\n",
        "backend/pyproject.toml": '[project]\nname="test"\nversion="0.5.0"\n',
        "backend/uv.lock": "version = 1\n",
        "frontend/package.json": '{"version":"0.5.0"}',
        "frontend/bun.lock": "{}",
        "scripts/run-service.py": "# test launcher\n",
        ".gitignore": "data/\ndist/\n.runtime.json\n.local-password\nwork/\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(content)
    _ = git(root, "init", "-q")
    _ = git(root, "add", ".")
    _ = git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "测试材料",
    )
    commit = git(root, "rev-parse", "HEAD")
    (root / "backend/data").mkdir()
    with closing(sqlite3.connect(root / "backend/data/paper.sqlite3")) as connection:
        _ = connection.execute("CREATE TABLE entries(id INTEGER PRIMARY KEY, amount TEXT)")
        _ = connection.execute("INSERT INTO entries VALUES(1, '100.01')")
        connection.commit()
    for name, content in (
        (".runtime.json", "{}"),
        (".local-password", "secret-test-password-never-log"),
    ):
        path = root / "backend" / name
        _ = path.write_text(content)
        path.chmod(0o600)
    reference = tmp / "reference"
    for path in (root / "frontend/dist", reference):
        path.mkdir()
        _ = (path / "index.html").write_text("<html>test</html>")
    private = tmp / "private"
    private.mkdir(mode=0o700)
    return Fixture(root, reference, private / "backup", commit)


def snapshot(path: Path) -> dict[str, tuple[bytes, int, int]]:
    return {
        str(p.relative_to(path)): (
            p.read_bytes(),
            p.stat().st_mtime_ns,
            p.stat().st_mode,
        )
        for p in path.rglob("*")
        if p.is_file()
    }


def assert_failure(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0
    assert "secret-test-password-never-log" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
