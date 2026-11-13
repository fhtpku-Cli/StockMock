"""备份失败的固定安全标签和人工处理说明。"""

from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import subprocess
import tarfile
from typing import Final, Literal

Stage = Literal[
    "未知",
    "源码检查",
    "源码复查",
    "运行材料读取",
    "运行材料复查",
    "依赖材料读取",
    "依赖材料复查",
    "静态材料读取",
    "静态材料复查",
    "材料复制",
    "源码归档",
    "数据库备份",
    "备份包检查",
]
Material = Literal[
    "未知",
    "源码",
    "依赖清单与锁文件",
    "运行配置",
    "登录密码",
    "外部规则",
    "前端静态材料",
    "运行材料",
    "数据库",
    "备份包",
]
Check = Literal["未知", "读取前后状态变化", "与Git内容不一致", "前后内容不一致", "提交前后不一致"]


@dataclass(frozen=True, slots=True)
class FailureContext:
    stage: Stage = "未知"
    material: Material = "未知"
    check: Check = "未知"


UNKNOWN_CONTEXT: Final = FailureContext()


class BackupError(RuntimeError):
    """仅携带固定安全错误码与固定上下文；不接收第三方错误正文。"""

    def __init__(self, code: str, context: FailureContext = UNKNOWN_CONTEXT) -> None:
        super().__init__(code)
        self.context: FailureContext = context


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


def failure_message(error: BackupError) -> str:
    context = error.context
    guidance = (
        "请核对上述材料并确认其在备份期间保持稳定。"
        if safe_code(error) == "source_changed"
        else diagnosis(safe_code(error))
    )
    return (
        f"[{safe_code(error)}] 阶段：{context.stage}；材料：{context.material}；检查：{context.check}。"
        "仅说明本次发现不一致或失败的检查环节，不代表已确定根因。"
        f"{guidance} 本次没有完整成功，失败包不能用于恢复。"
        "请检查对应材料后，人工使用尚不存在的新目标路径重试；父目录须已存在、归本人所有且权限为0700，"
        "不要提前创建目标目录；不覆盖、续写或清理旧包。"
    )
