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
    match code:
        case "dirty_source":
            return "应用文件与已确认的版本不一致。请让维护者核对文件和版本。"
        case "untracked_source":
            return "应用中有尚未确认版本的文件。请让维护者确认这些文件是否需要保留。"
        case "source_changed":
            return "读取文件时发现它发生了变化，目前无法确认原因。请让维护者确认文件稳定后再试。"
        case "runtime_changed" | "static_changed" | "database_source_changed":
            return "备份时文件发生了变化，目前无法确认原因。请让维护者确认文件稳定后再试。"
        case "static_source_mismatch" | "static_reference_mismatch" | "version_mismatch" | "archive_commit_mismatch":
            return "应用文件的版本没有配套一致。请让维护者核对应用与页面文件的版本。"
        case "source_commit" | "static_commit":
            return "无法确认指定的应用版本。请让维护者核对所填的版本信息。"
        case "reference_same_directory":
            return "用来核对的页面文件与待备份文件是同一份，无法互相验证。请让维护者提供另一份已确认的页面文件。"
        case "source_command_timeout":
            return "核对或打包应用文件等了太久，未能完成。请让维护者查看原因后再试。"
        case "source_command_failed":
            return "未能完成应用文件的核对或打包，目前无法确认原因。请让维护者查看原因。"
        case "destination_source_overlap" | "destination_project_location":
            return "这个保存位置会与应用文件混在一起，不能用于保存备份。请换一个专门存放备份的位置。"
        case "directory_permissions" | "file_permissions":
            return "文件或文件夹的访问权限不符合备份要求。请让维护者调整为仅本人可访问。"
        case "directory_owner_or_type" | "file_owner_or_type":
            return "所选位置不属于当前用户，或不是需要的文件或文件夹。请让维护者核对所选位置。"
        case "symbolic_link":
            return "所选文件或文件夹是通往其他位置的链接，不能直接使用。请让维护者改用真实存放位置。"
        case "path_parent_component" | "manifest_path" | "archive_member":
            return "文件位置或排列方式不符合备份要求。请保留原文件，让维护者核对。"
        case "material_permissions":
            return "当前用户没有权限读取或保存所需文件。请让维护者核对访问权限。"
        case "material_missing" | "archive_required" | "package_required":
            return "找不到需要的文件或文件夹。请让维护者核对所选位置，并补齐缺少的文件。"
        case "config_fields":
            return "应用设置中有备份工具不认识的内容。请让维护者核对设置。"
        case "password_length":
            return "保存的登录密码长度不足，未达到备份要求。请让维护者核对密码设置。"
        case "rules_path":
            return "交易规则文件的存放位置没有填写。请让维护者补齐设置。"
        case "rules_mapping" | "restore_mapping":
            return "备份中的文件与恢复时需要放回的位置对不上。请让维护者核对备份内容。"
        case "database_timeout":
            return "复制账户数据等了太久，未能完成。请让维护者确认原因后再试。"
        case "database_sidecar":
            return "账户数据旁还有单独保存的记录文件，暂时无法确认数据是否完整。请保留原件，让维护者核对。"
        case "database_integrity" | "database_operation_failed":
            return "账户数据无法正常读取，或没能确认数据是否完整。请保留原件，让维护者核对数据。"
        case "database_empty":
            return "没有找到需要保存的账户记录。请让维护者核对是否选对了账户数据文件。"
        case "database_tables":
            return "账户数据与备份中记录的数量对不上。请保留原件，让维护者核对。"
        case "incomplete_package":
            return "这个文件夹缺少成功完成备份的记录，或留有失败记录。"
        case "package_file_set":
            return "备份中的文件或文件大小与备份记录对不上。"
        case "material_format_invalid":
            return "文件内容的格式无法识别。请保留原件，让维护者核对。"
        case _:
            return "读取或保存文件时未能完成，目前无法确认具体原因。请让维护者查看原因。"


def failure_message(error: BackupError) -> str:
    code = safe_code(error)
    if code == "destination_exists":
        return (
            "保存位置已有同名文件或文件夹，本次没有覆盖它，也没有创建新备份。"
            "请在已有、仅本人可访问的文件夹里换一个尚未使用的备份名称，不要提前创建这个新文件夹。"
        )
    guidance = diagnosis(code)
    if code == "source_changed" and error.context.check == "与Git内容不一致":
        guidance = diagnosis("dirty_source")
    return (
        f"{guidance} 这次备份未完成，不能用于恢复。"
        "问题处理好后，请在已有、仅本人可访问的文件夹里换一个尚未使用的备份名称再试；"
        "不要提前创建这个新文件夹，也不要覆盖或删除已有备份。"
    )


def inspect_failure_message(code: str) -> str:
    return f"{diagnosis(code)} 目前无法确认这份备份能否用于恢复，请暂时不要用它恢复。请保留原件，让维护者核对后再试。"
