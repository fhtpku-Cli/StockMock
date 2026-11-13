"""只读检查、归档身份和配套关系的真实 CLI 验证。"""

from __future__ import annotations
import json
from pathlib import Path
import pytest
from test_backup_fixture import assert_failure, git, setup, snapshot


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "manifest",
        "manifest_status",
        "manifest_verification",
        "database",
        "sidecar",
        "permissions",
        "extra",
        "tar_commit",
        "tar_required",
        "tar_unsafe",
        "version",
        "mapping",
    ],
)
def test_inspect_rejects_without_writing(tmp_path: Path, case: str) -> None:
    sample = setup(tmp_path)
    assert sample.create().returncode == 0
    manifest_path = sample.destination / "result.json"
    manifest = json.loads(manifest_path.read_text())
    match case:
        case "missing":
            (sample.destination / "runtime/password").unlink()
        case "manifest":
            _ = manifest_path.write_text("{}")
        case "manifest_status" | "manifest_verification":
            del manifest["status" if case == "manifest_status" else "static_verification"]
            _ = manifest_path.write_text(json.dumps(manifest))
        case "database":
            path = sample.destination / "paper.sqlite3"
            _ = path.write_bytes(b"x" * path.stat().st_size)
        case "sidecar":
            path = sample.destination / "paper.sqlite3-wal"
            _ = path.write_bytes(b"wal")
            path.chmod(0o600)
            manifest["files"][path.name] = 3
            _ = manifest_path.write_text(json.dumps(manifest))
        case "permissions":
            (sample.destination / "runtime").chmod(0o755)
        case "extra":
            path = sample.destination / "unexpected"
            _ = path.write_text("x")
            path.chmod(0o600)
        case "tar_commit":
            _ = (sample.root / "backend/app/main.py").write_text("changed")
            _ = git(sample.root, "add", ".")
            _ = git(
                sample.root,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "另一次提交",
            )
            _ = git(
                sample.root,
                "archive",
                "--output=" + str(sample.destination / "source.tar"),
                "HEAD",
            )
        case "tar_required" | "tar_unsafe":
            import io
            import tarfile

            path = sample.destination / "source.tar"
            with tarfile.open(path, "w", pax_headers={"comment": sample.commit}) as package:
                item = tarfile.TarInfo("../outside" if case == "tar_unsafe" else "only")
                item.size = 1
                package.addfile(item, io.BytesIO(b"x"))
            manifest["files"]["source.tar"] = path.stat().st_size
            _ = manifest_path.write_text(json.dumps(manifest))
        case "version":
            manifest["version"] = "wrong"
            _ = manifest_path.write_text(json.dumps(manifest))
        case "mapping":
            manifest["restore_paths"]["paper.sqlite3"] = "../outside"
            _ = manifest_path.write_text(json.dumps(manifest))
    before = snapshot(sample.destination)
    assert_failure(sample.inspect())
    assert snapshot(sample.destination) == before


def test_rules_copied_and_original_not_needed_for_inspect(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    rules = tmp_path.resolve() / "private-rules.json"
    _ = rules.write_text('{"value":"secret-test-password-never-log"}')
    config = {"STOCKMOCK_INSTRUMENTS_FILE": str(rules)}
    _ = (sample.root / "backend/.runtime.json").write_text(json.dumps(config))
    result = sample.create()
    assert result.returncode == 0, result.stderr
    assert "secret-test-password-never-log" not in result.stdout + result.stderr
    assert (sample.destination / "runtime/instruments.json").read_bytes() == rules.read_bytes()
    rules.unlink()
    assert sample.inspect().returncode == 0


def test_frontend_source_mismatch_but_document_change_allowed(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    _ = (sample.root / "frontend/README.md").write_text("文档")
    _ = git(sample.root, "add", ".")
    _ = git(
        sample.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "文档",
    )
    assert sample.create().returncode == 0
    _ = (sample.root / "frontend/bun.lock").write_text("different")
    _ = git(sample.root, "add", ".")
    _ = git(
        sample.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "源码",
    )
    assert_failure(sample.create("--destination", str(sample.destination.with_name("another"))))


def test_invalid_database_retains_partial_files(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    _ = (sample.root / "backend/data/paper.sqlite3").write_bytes(b"invalid database")
    assert_failure(sample.create())
    assert (sample.destination / "runtime/password").is_file()
    assert (sample.destination / "source.tar").is_file()
    assert (sample.destination / "failure.json").is_file()
    assert not (sample.destination / "result.json").exists()


def test_failed_new_package_preserves_previous_success(tmp_path: Path) -> None:
    sample = setup(tmp_path)
    assert sample.create().returncode == 0
    before = snapshot(sample.destination)
    _ = (sample.root / "backend/data/paper.sqlite3").write_bytes(b"invalid")
    result = sample.create("--destination", str(sample.destination.with_name("failed-new")))
    assert_failure(result)
    assert snapshot(sample.destination) == before
    assert sample.inspect().returncode == 0
