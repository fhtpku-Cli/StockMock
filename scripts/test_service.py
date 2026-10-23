"""隔离验证后台启动配置，不调用真实 launchctl、不接触个人数据库。"""

import importlib.util
import io
import json
import logging
import os
import plistlib
import subprocess
import sys
import tempfile
import shutil
import sqlite3
import unittest
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
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

    def test_runner_pins_loopback_proxy_trust(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "backend").mkdir()
            password = root / "backend/.local-password"
            password.write_text("isolated-test-password")
            password.chmod(0o600)
            previous_mask = os.umask(0o077)
            try:
                with (
                    # 此测试执行通用启动器；第三方依赖必须按真实系统导入。
                    patch.object(sys, "platform", REAL_PLATFORM),
                    patch.object(sys, "path", [str(HERE.parent / "backend"), *sys.path]),
                    patch.object(runtime, "ROOT", root),
                    patch.object(runtime, "configure_logging"),
                    patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": "*"}),
                    patch("app.main.create_app") as factory,
                    patch("uvicorn.run") as run,
                ):
                    runtime.main()
                    self.assertEqual(run.call_args.args, (factory.return_value,))
                    self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
                    self.assertTrue(run.call_args.kwargs["proxy_headers"])
                    self.assertEqual(run.call_args.kwargs["forwarded_allow_ips"], "127.0.0.1")
                    self.assertEqual(run.call_args.kwargs["workers"], 1)
            finally:
                os.umask(previous_mask)

    def test_plist_is_portable_and_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "包含空格的 项目"
            payload = service.plist(root)
            path = Path(temp) / "service.plist"
            path.write_bytes(plistlib.dumps(payload))
            self.assertEqual(plistlib.loads(path.read_bytes()), payload)
            if REAL_PLATFORM == "darwin":
                result = subprocess.run(["plutil", "-lint", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(payload["ProgramArguments"][0], str(root / "backend/.venv/bin/python"))
            self.assertNotIn("EnvironmentVariables", payload)
            self.assertTrue(payload["KeepAlive"])

    def test_private_json_rejects_commands_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "backend").mkdir()
            config = root / "backend/.runtime.json"
            config.write_text(json.dumps({"PAPER_ALLOWED_HOSTS": "localhost,private.ts.net"}))
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
            patch.object(service, "invoke", return_value=subprocess.CompletedProcess([], 1)) as calls,
            patch("sys.argv", ["service.py", "install"]),
        ):
            with self.assertRaises(SystemExit):
                service.main()
            self.assertEqual(calls.call_count, 1)
            self.assertEqual(calls.call_args.args[0][1], "print")
            self.assertFalse((Path(temp) / "Library/LaunchAgents").exists())

    def test_stop_waits_for_owned_job_to_disappear(self):
        for action in ("stop", "uninstall"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                agent = root / "Library/LaunchAgents" / f"{service.LABEL}.plist"
                agent.parent.mkdir(parents=True)
                agent.write_bytes(plistlib.dumps(service.plist(root)))
                results = [subprocess.CompletedProcess([], code) for code in (0, 0, 0, 0, 113)]
                with (
                    patch.object(service, "ROOT", root),
                    patch.object(service.Path, "home", return_value=root),
                    patch.object(service, "invoke", side_effect=results) as calls,
                    patch("sys.argv", ["service.py", action]),
                ):
                    service.main()
                self.assertEqual(
                    [call.args[0][1] for call in calls.call_args_list], ["print", "bootout", "print", "print", "print"]
                )
                self.assertEqual(agent.exists(), action == "stop")

    def test_uninstall_preserves_plist_when_stop_cannot_be_confirmed(self):
        for final_code in (0, 5):
            with self.subTest(final_code=final_code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                agent = root / "Library/LaunchAgents" / f"{service.LABEL}.plist"
                agent.parent.mkdir(parents=True)
                agent.write_bytes(plistlib.dumps(service.plist(root)))
                results = [subprocess.CompletedProcess([], code) for code in (0, 0, final_code)]
                output = io.StringIO()
                with (
                    patch.object(service, "ROOT", root),
                    patch.object(service.Path, "home", return_value=root),
                    patch.object(service, "invoke", side_effect=results),
                    patch("time.monotonic", side_effect=[0, 11]),
                    patch("sys.argv", ["service.py", "uninstall"]),
                    redirect_stdout(output),
                ):
                    with self.assertRaises(SystemExit):
                        service.main()
                self.assertTrue(agent.exists())
                self.assertNotIn("服务已停止", output.getvalue())

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
                    service, "invoke", side_effect=[subprocess.CompletedProcess([], code) for code in (0, 0, 113)]
                ) as calls,
                patch("sys.argv", ["service.py", "uninstall"]),
            ):
                service.main()
            self.assertFalse(agent.exists())
            self.assertTrue(database.exists())
            self.assertTrue(password.exists())
            self.assertEqual([call.args[0][1] for call in calls.call_args_list], ["print", "bootout", "print"])

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

    def test_install_and_start_relay_only_safe_diagnostic_category(self):
        for action in ("install", "start"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                for item in ("backend/.venv/bin/python", "frontend/dist/index.html"):
                    target = root / item
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
                output = io.StringIO()
                with (
                    patch.object(service, "ROOT", root),
                    patch.object(service.Path, "home", return_value=root),
                    patch.object(service, "port_busy", return_value=False),
                    patch.object(
                        service,
                        "invoke",
                        side_effect=[
                            subprocess.CompletedProcess([], 113),
                            subprocess.CompletedProcess([], 1, stderr="[password_file] safe\nPRIVATE_SENTINEL"),
                        ],
                    ) as calls,
                    patch("sys.argv", ["service.py", action]),
                    redirect_stderr(output),
                ):
                    with self.assertRaises(SystemExit):
                        service.main()
                self.assertIn("密码缺失或权限不安全", output.getvalue())
                self.assertNotIn("PRIVATE_SENTINEL", output.getvalue())
                self.assertEqual(calls.call_count, 2)

    def test_logs_rotate_with_bounded_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            handler = runtime.configure_logging(folder)
            try:
                for _ in range(4500):
                    handler.emit(logging.LogRecord("test", logging.INFO, "test", 1, "x" * 1000, (), None))
                self.assertLessEqual(len(list(folder.glob("service.log*"))), 4)
                self.assertTrue((folder / "service.log.3").exists())
                self.assertTrue(all(path.stat().st_size < 1_002_000 for path in folder.iterdir()))
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


class RuntimeProcessTests(unittest.TestCase):
    """真实子进程使用隔离根；审计钩子拒绝检查路径联网或监听。"""

    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix="stockmock-runtime-"))
        self.root = Path(self.temp)
        (self.root / "scripts").mkdir()
        (self.root / "backend").mkdir()
        shutil.copy(HERE / "run-service.py", self.root / "scripts/run-service.py")
        (self.root / "backend/app").symlink_to(HERE.parent / "backend/app", target_is_directory=True)
        self.password = self.root / "backend/.local-password"
        self.password.write_text("Q041_PRIVATE_SENTINEL_PASSWORD")
        self.password.chmod(0o600)
        self.config = self.root / "backend/.runtime.json"
        self.config.write_text("{}")
        self.config.chmod(0o600)
        self.database = self.root / "backend/data/paper.sqlite3"

    def execute(self, *, isolated_python=False):
        program = """
import runpy, sys, threading
from pathlib import Path
def guard(event, args):
    if event in {'socket.connect', 'socket.bind'}:
        raise RuntimeError('配置检查禁止网络')
sys.addaudithook(guard)
def observe(frame, event, arg):
    if event == 'call' and Path(frame.f_code.co_filename).name in {'worker.py', 'plan_worker.py'}:
        if frame.f_code.co_name in {'run', 'tick'}:
            raise RuntimeError('配置检查启动业务worker')
sys.setprofile(observe)
sys.argv = [sys.argv[1], '--check-config']
runpy.run_path(sys.argv[0], run_name='__main__')
assert all(t.name == 'MainThread' or t.name.startswith('quickjs-worker-') for t in threading.enumerate())
print('NETWORK_AND_WORKER_GUARD_PASSED')
"""
        command = [
            sys.executable,
            *(["-S"] if isolated_python else []),
            "-c",
            program,
            str(self.root / "scripts/run-service.py"),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        evidence = os.environ.get("STOCKMOCK_TEST_EVIDENCE")
        if evidence:
            folder = Path(evidence) / self.id().split(".")[-1]
            folder.mkdir(parents=True, exist_ok=True)
            sequence = len(list(folder.glob("*.json")))
            logs = {
                path.name: path.read_text()
                for path in (self.root / "backend/data/logs").glob("service.log*")
                if not path.is_symlink()
            }
            (folder / f"{sequence}.json").write_text(
                json.dumps(
                    {
                        "invocation": "隔离 Python 子进程 run-service.py --check-config（网络/业务worker观察器启用）",
                        "exit": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "logs": logs,
                        "database_exists": self.database.exists(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return result

    def assert_safe_failure(self, stage, *, isolated_python=False):
        result = self.execute(isolated_python=isolated_python)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"[{stage}]", result.stderr)
        logs = "".join(path.read_text() for path in (self.root / "backend/data/logs").glob("service.log*"))
        for secret in ("Q041_PRIVATE_SENTINEL", "Traceback", "secret.example.invalid"):
            self.assertNotIn(secret, result.stdout + result.stderr + logs)
        self.assertIn(f"[{stage}]", logs)
        self.assertTrue(
            all(path.stat().st_mode & 0o777 == 0o600 for path in (self.root / "backend/data/logs").glob("service.log*"))
        )

    def test_check_no_database_creates_no_account_or_workers(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NETWORK_AND_WORKER_GUARD_PASSED", result.stdout)
        self.assertFalse(self.database.exists())

    def test_check_legacy_database_does_not_migrate_original(self):
        self.database.parent.mkdir()
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE preserved(id INTEGER PRIMARY KEY, amount TEXT)")
            connection.execute("INSERT INTO preserved VALUES(1, '123.45')")
        original = self.database.read_bytes()
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.database.read_bytes(), original)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(
                connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("preserved",)]
            )

    def test_dynamic_paused_symbol_is_checked_in_isolated_copy(self):
        self.database.parent.mkdir()
        rule = {
            "symbol": "ZZTEST",
            "name": "隔离测试证券",
            "provider_code": "usZZTEST",
            "market": "US",
            "currency": "USD",
            "kind": "stock",
            "lot_size": 1,
            "tick_size": "0.01",
            "settlement_days": 0,
            "tradable": True,
            "restriction": "测试",
            "tick_rule": "US",
            "rule_source": "受控测试",
            "verified_on": "2026-10-24",
        }
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE activated_instruments(symbol TEXT PRIMARY KEY, data TEXT NOT NULL)")
            connection.execute("INSERT INTO activated_instruments VALUES(?, ?)", ("ZZTEST", json.dumps(rule)))
        original = self.database.read_bytes()
        self.config.write_text(json.dumps({"PAPER_PAUSED_SYMBOLS": "ZZTEST"}))
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.database.read_bytes(), original)

    def test_invalid_config_types_whitelist_hosts_origins_and_rules(self):
        cases = [
            ("{Q041_PRIVATE_SENTINEL", "config_format"),
            (json.dumps({"PAPER_ALLOWED_HOSTS": 42}), "config_format"),
            (json.dumps({"command": "Q041_PRIVATE_SENTINEL"}), "config_format"),
            (json.dumps({"PAPER_ALLOWED_HOSTS": "*.secret.example.invalid"}), "initialization"),
            (json.dumps({"PAPER_ALLOWED_ORIGINS": "http://secret.example.invalid"}), "initialization"),
            (json.dumps({"PAPER_PAUSED_SYMBOLS": "UNKNOWN"}), "initialization"),
            (json.dumps({"STOCKMOCK_INSTRUMENTS_FILE": str(self.root / "missing-rules")}), "initialization"),
        ]
        for payload, stage in cases:
            with self.subTest(stage=stage, payload_kind=payload[:15]):
                self.config.write_text(payload)
                self.assert_safe_failure(stage)

    def test_private_file_permissions_symlink_and_password_length(self):
        self.config.chmod(0o644)
        self.assert_safe_failure("config_file")
        self.config.unlink()
        self.config.symlink_to(self.root / "missing")
        self.assert_safe_failure("config_file")
        self.config.unlink()
        self.password.chmod(0o644)
        self.assert_safe_failure("password_file")
        self.password.chmod(0o600)
        self.password.write_text("short")
        self.assert_safe_failure("password_value")
        self.password.unlink()
        self.assert_safe_failure("password_file")

    def test_active_wal_database_remains_unchanged(self):
        self.database.parent.mkdir()
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE preserved(id INTEGER PRIMARY KEY, amount TEXT)")
            connection.execute("INSERT INTO preserved VALUES(1, '789.01')")
            connection.commit()
            before = list(connection.iterdump())
            files = {
                p.name: p.read_bytes()
                for p in self.database.parent.glob("paper.sqlite3*")
                if not p.name.endswith("-shm")
            }
            result = self.execute()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(connection.iterdump()), before)
            self.assertEqual(
                {
                    p.name: p.read_bytes()
                    for p in self.database.parent.glob("paper.sqlite3*")
                    if not p.name.endswith("-shm")
                },
                files,
            )

    def test_locked_database_fails_within_bounded_time(self):
        self.database.parent.mkdir()
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE preserved(id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.execute("BEGIN EXCLUSIVE")
            self.assert_safe_failure("initialization")
        self.assertEqual(self.execute().returncode, 0)

    def test_existing_logs_permissions_and_symlinks(self):
        logs = self.root / "backend/data/logs"
        logs.mkdir(parents=True)
        logs.chmod(0o755)
        logfile = logs / "service.log"
        logfile.write_text("历史日志\n")
        logfile.chmod(0o644)
        self.assertEqual(self.execute().returncode, 0)
        self.assertEqual(logs.stat().st_mode & 0o777, 0o700)
        self.assertEqual(logfile.stat().st_mode & 0o777, 0o600)
        logfile.unlink()
        logfile.symlink_to(self.password)
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[logging]", result.stderr)
        self.assertEqual(self.password.read_text(), "Q041_PRIVATE_SENTINEL_PASSWORD")
        logfile.unlink()
        logs.chmod(0o500)
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[logging]", result.stderr)
        logs.chmod(0o700)
        logs.rmdir()
        logs.symlink_to(self.root / "backend")
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[logging]", result.stderr)

    def test_dependency_and_database_failure_are_safe(self):
        self.assert_safe_failure("dependency", isolated_python=True)
        self.database.parent.mkdir(exist_ok=True)
        self.database.write_text("Q041_PRIVATE_SENTINEL_DATABASE")
        self.assert_safe_failure("initialization")

    def test_unwritable_logs_fail_safely_and_valid_config_recovers(self):
        (self.root / "backend/data").mkdir()
        logs = self.root / "backend/data/logs"
        logs.write_text("not a directory")
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[logging]", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        logs.unlink()
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
