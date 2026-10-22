#!/usr/bin/env python3
"""macOS 当前用户后台服务生命周期；不会终止端口上的其他程序。"""

from __future__ import annotations
import argparse
import json
import os
import plistlib
import re
import socket
import subprocess
import sys
from pathlib import Path
from pydantic import TypeAdapter


class Arguments(argparse.Namespace):
    """命令行参数的具体类型。"""

    action: str = "status"
    dry_run: bool = False


LABEL = "local.stockmock.personal"
ROOT = Path(__file__).resolve().parents[1]


def plist(root: Path) -> dict[str, str | bool | int | list[str]]:
    """生成无密码、无可变依赖安装步骤的服务定义。"""
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(root / "backend/.venv/bin/python"),
            str(root / "scripts/run-service.py"),
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def port_busy() -> bool:
    """端口被占用时拒绝启动，而非抢占或杀进程。"""
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", 8000)) == 0


def invoke(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """仅执行固定程序与参数，不启用 shell。"""
    return subprocess.run(args, check=check, text=True, capture_output=True)


def main() -> None:
    """安装、启动、查询、停止或移除当前用户服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "action", choices=["install", "start", "status", "stop", "uninstall"]
    )
    _ = parser.add_argument(
        "--dry-run", action="store_true", help="仅显示计划，不修改主机"
    )
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    target = f"gui/{os.getuid()}/{LABEL}"
    if args.dry_run:
        print(
            json.dumps(
                {
                    "操作": args.action,
                    "服务": target,
                    "路径": str(path),
                    "配置": plist(ROOT),
                    "端口占用": port_busy(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if sys.platform != "darwin":
        parser.error("此工具仅用于 macOS 当前用户 LaunchAgent")
    result = invoke(["launchctl", "print", target], check=False)
    loaded = result.returncode == 0
    owned = False
    if path.exists() and not path.is_symlink():
        try:
            with path.open("rb") as stream:
                owned = TypeAdapter(
                    dict[str, str | bool | int | list[str]]
                ).validate_python(plistlib.load(stream), strict=True) == plist(ROOT)
        except (plistlib.InvalidFileException, ValueError, OSError):
            owned = False
    process_match = re.search(r"\bpid = (\d+)", result.stdout or "")
    if args.action == "status":
        print(
            json.dumps(
                {
                    "已安装": path.exists(),
                    "已加载": loaded,
                    "属于当前项目": owned,
                    "进程PID": process_match.group(1) if process_match else None,
                    "正在运行": bool(
                        re.search(r"state = running", result.stdout or "")
                    ),
                    "本机8000端口可连接": port_busy(),
                },
                ensure_ascii=False,
            )
        )
        return
    if (path.exists() or loaded) and not owned:
        parser.error(
            "同名服务不属于当前项目或无法核对；拒绝停止、删除、覆盖或复用，请人工检查"
        )
    if args.action in {"stop", "uninstall"}:
        if loaded:
            _ = invoke(["launchctl", "bootout", target])
        if args.action == "uninstall":
            path.unlink(missing_ok=True)
        print("服务已停止；数据库、密码和日志均保留。")
        return
    if loaded:
        print("服务已加载；没有重复启动。")
        return
    if port_busy():
        parser.error(
            "127.0.0.1:8000 已被占用。请自行停止旧实例后重试；不会终止其他进程。"
        )
    for item in (
        ROOT / "backend/.venv/bin/python",
        ROOT / "frontend/dist/index.html",
        ROOT / "backend/.local-password",
    ):
        if not item.is_file():
            parser.error(f"缺少运行文件：{item}")
    preflight = invoke(
        [
            str(ROOT / "backend/.venv/bin/python"),
            str(ROOT / "scripts/run-service.py"),
            "--check-config",
        ],
        check=False,
    )
    if preflight.returncode:
        parser.error(
            "运行配置检查失败。请核对密码权限、.runtime.json 和依赖；未启动服务。"
        )
    if args.action == "install":
        if path.exists():
            parser.error("服务文件已存在；请先检查现有配置，或卸载后重装")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            plistlib.dump(plist(ROOT), stream)
        path.chmod(0o600)
    elif not path.exists():
        parser.error("请先运行 install 安装服务")
    else:
        with path.open("rb") as stream:
            if plistlib.load(stream) != plist(ROOT):
                parser.error("现有服务不属于当前项目配置，请先人工核对")
    _ = invoke(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
    print("服务已加载；请运行 status 并访问本机页面验证。当前用户登录后自动运行。")


if __name__ == "__main__":
    main()
