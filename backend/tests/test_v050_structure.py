"""0.5.0 模块级代码结构比对：只验证新增范围，不替代行为测试。"""

import ast
import subprocess
from pathlib import Path

BASE = "22e18fa"
ROOT = Path(__file__).resolve().parents[2]


def source_at(path: str, revision: str | None = None) -> str:
    if revision is None:
        return (ROOT / path).read_text(encoding="utf-8")
    return subprocess.run(  # noqa: S603 只读取固定基准版本的仓库文件，不执行外部输入。
        ["git", "show", f"{revision}:{path}"],  # noqa: S607
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def module_at(path: str, revision: str | None = None) -> ast.Module:
    return ast.parse(source_at(path, revision))


def bound_names(node: ast.stmt) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Import | ast.ImportFrom):
        names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        names.add(node.name)
    elif isinstance(node, ast.Assign):
        names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.add(node.target.id)
    return names


def class_named(module: ast.Module, name: str) -> ast.ClassDef:
    return next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == name)


def assert_class_extension(path: str, name: str) -> None:
    old, new = module_at(path, BASE), module_at(path)
    old_class, new_class = class_named(old, name), class_named(new, name)
    assert old_class.name == new_class.name
    assert [ast.dump(node) for node in old_class.bases] == [ast.dump(node) for node in new_class.bases]
    assert [ast.dump(node) for node in old_class.decorator_list] == [
        ast.dump(node) for node in new_class.decorator_list
    ]
    assert [ast.dump(node) for node in old_class.body] == [
        ast.dump(node) for node in new_class.body[: len(old_class.body)]
    ]
    assert all(isinstance(node, ast.FunctionDef) for node in new_class.body[len(old_class.body) :])


def test_marketdata_and_provider_keep_old_structure() -> None:
    for path, class_name in (
        ("backend/app/marketdata.py", "MarketDataClient"),
        ("backend/app/activated_registry.py", "RuleProvider"),
    ):
        old, new = module_at(path, BASE), module_at(path)
        comparable = [node for node in old.body if getattr(node, "name", None) != class_name]
        new_dumps = [ast.dump(node) for node in new.body]
        assert all(ast.dump(node) in new_dumps for node in comparable)
        old_names: set[str] = set()
        for node in old.body:
            old_names.update(bound_names(node))
        old_dumps = {ast.dump(node) for node in old.body}
        for node in new.body:
            if ast.dump(node) in old_dumps or getattr(node, "name", None) == class_name:
                continue
            assert isinstance(node, ast.ImportFrom)
            assert not bound_names(node) & old_names
        assert_class_extension(path, class_name)


def test_models_only_add_change_status_and_position_fields() -> None:
    old, new = module_at("backend/app/models.py", BASE), module_at("backend/app/models.py")
    new_dumps = [ast.dump(node) for node in new.body]
    for node in old.body:
        if getattr(node, "name", None) != "PositionView":
            assert ast.dump(node) in new_dumps
    old_dumps = {ast.dump(node) for node in old.body}
    additions = [
        node
        for node in new.body
        if ast.dump(node) not in old_dumps and getattr(node, "name", None) != "PositionView"
    ]
    assert len(additions) == 1
    assert isinstance(additions[0], ast.Assign)
    targets = [target for target in additions[0].targets if isinstance(target, ast.Name)]
    assert [target.id for target in targets] == ["ChangeStatus"]
