"""隔离验证后台启动配置，不调用真实 launchctl、不接触个人数据库。"""

import importlib.util
import json
import logging
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REAL_PLATFORM = sys.platform
HERE = Path(__file__).resolve().parent


def load(name: str, filename: str):
    """加载带连字符的独立启动脚本。"""
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("测试脚本加载失败")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service = load("service", "service.py")
runtime = load("run_service", "run-service.py")


class ServiceTests(unittest.TestCase):
    """所有服务写入限于临时用户目录，外部程序调用被记录替身替代。"""

    def setUp(self):
        self.enterContext(patch.object(service.sys, "platform", "darwin"))

    def test_plist_is_portable_and_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "包含空格的 项目"
            payload = service.plist(root)
            path = Path(temp) / "service.plist"
            path.write_bytes(plistlib.dumps(payload))
            self.assertEqual(plistlib.loads(path.read_bytes()), payload)
            if REAL_PLATFORM == "darwin":
                result = subprocess.run(
                    ["plutil", "-lint", str(path)], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                payload["ProgramArguments"][0], str(root / "backend/.venv/bin/python")
            )
            self.assertNotIn("EnvironmentVariables", payload)
            self.assertTrue(payload["KeepAlive"])

    def test_private_json_rejects_commands_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "backend").mkdir()
            config = root / "backend/.runtime.json"
            config.write_text(
                json.dumps({"PAPER_ALLOWED_HOSTS": "localhost,private.ts.net"})
            )
            config.chmod(0o600)
            self.assertEqual(
                runtime.configuration(root)["PAPER_ALLOWED_HOSTS"],
                "localhost,private.ts.net",
            )
            config.write_text(json.dumps({"command": "echo unsafe"}))
            with self.assertRaises(RuntimeError):
                runtime.configuration(root)
            config.write_text("{}")
            config.chmod(0o644)
            with self.assertRaises(RuntimeError):
                runtime.configuration(root)

    def test_port_conflict_never_bootstraps_or_kills(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(service.Path, "home", return_value=Path(temp)),
            patch.object(service, "port_busy", return_value=True),
            patch.object(
                service, "invoke", return_value=subprocess.CompletedProcess([], 1)
            ) as calls,
            patch("sys.argv", ["service.py", "install"]),
        ):
            with self.assertRaises(SystemExit):
                service.main()
            self.assertEqual(calls.call_count, 1)
            self.assertEqual(calls.call_args.args[0][1], "print")
            self.assertFalse((Path(temp) / "Library/LaunchAgents").exists())

    def test_uninstall_preserves_database_and_password(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / "Library/LaunchAgents" / f"{service.LABEL}.plist"
            agent.parent.mkdir(parents=True)
            agent.write_bytes(plistlib.dumps(service.plist(root)))
            database = root / "paper.sqlite3"
            database.write_text("保存数据")
            password = root / ".local-password"
            password.write_text("测试密码文件")
            with (
                patch.object(service, "ROOT", root),
                patch.object(service.Path, "home", return_value=root),
                patch.object(
                    service, "invoke", return_value=subprocess.CompletedProcess([], 0)
                ) as calls,
                patch("sys.argv", ["service.py", "uninstall"]),
            ):
                service.main()
            self.assertFalse(agent.exists())
            self.assertTrue(database.exists())
            self.assertTrue(password.exists())
            self.assertEqual(calls.call_args.args[0][1], "bootout")

    def test_foreign_checkout_service_cannot_be_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / "Library/LaunchAgents" / f"{service.LABEL}.plist"
            agent.parent.mkdir(parents=True)
            agent.write_bytes(plistlib.dumps(service.plist(root / "another-project")))
            with (
                patch.object(service.Path, "home", return_value=root),
                patch.object(
                    service,
                    "invoke",
                    return_value=subprocess.CompletedProcess([], 0, stdout="pid = 123"),
                ) as calls,
                patch("sys.argv", ["service.py", "uninstall"]),
            ):
                with self.assertRaises(SystemExit):
                    service.main()
            self.assertTrue(agent.exists())
            self.assertEqual(calls.call_count, 1)

    def test_logs_rotate_with_bounded_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            handler = runtime.configure_logging(folder)
            try:
                for _ in range(4500):
                    handler.emit(
                        logging.LogRecord(
                            "test", logging.INFO, "test", 1, "x" * 1000, (), None
                        )
                    )
                self.assertLessEqual(len(list(folder.glob("service.log*"))), 4)
                self.assertTrue((folder / "service.log.3").exists())
                self.assertTrue(
                    all(path.stat().st_size < 1_002_000 for path in folder.iterdir())
                )
            finally:
                handler.close()
                for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
                    logging.getLogger(name).removeHandler(handler)

    def test_dry_run_has_no_subprocess_writes(self):
        with (
            patch.object(service, "invoke") as calls,
            patch.object(service, "port_busy", return_value=False),
            patch("sys.argv", ["service.py", "install", "--dry-run"]),
        ):
            service.main()
        calls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
