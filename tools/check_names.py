"""未定义名检查器（AST）。

用法：
    python tools/check_names.py core ui main.py      # 通过则退出码 0

**为什么需要它：**
本项目的另外两种检查都抓不到这一类别：

  * `python -m compileall` —— 只做语法检查，不做名字解析
  * 离屏构造窗口 —— 只走构造函数，不触发任何数据路径

2026-10-06 一个被误删的 `QFont` import 就是这样溜过去的：`compileall` 通过、
离屏构造正常，直到真正载入词表才抛 `NameError`。而那个 `NameError` 又被
`except Exception` 吞掉、交给了模态弹窗，最终表现为"界面卡死"。

**定位：** 轻量自查工具，不追求完备。覆盖本项目这类普通函数/类结构即可。

**已知不检查：** 类作用域内的名字查找、动态 exec/eval、延迟求值的类型注解。
"""

import ast
import builtins
import sys
from pathlib import Path

BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
}


def bound_names(node, out):
    """收集一个节点里所有被绑定的名字（赋值目标、for 目标、with、导入别名…）。"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        out.add(node.name)
        return
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        out.add(node.id)
        return
    if isinstance(node, ast.arg):
        out.add(node.arg)
        return
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            out.add(alias.asname or alias.name.split(".")[0])
        return
    if isinstance(node, ast.ExceptHandler) and node.name:
        out.add(node.name)
        return
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        out.update(node.names)
        return
    for child in ast.iter_child_nodes(node):
        bound_names(child, out)


class ScopeChecker(ast.NodeVisitor):
    def __init__(self, module_names):
        self.module_names = module_names
        self.problems = []
        self.stack = []

    def _record(self, name, node):
        if name in BUILTINS or name in self.module_names:
            return
        for scope in self.stack:
            if name in scope:
                return
        self.problems.append((node.lineno, name))

    def _visit_scope(self, node):
        names = set()
        # 先把整层作用域内所有绑定收齐：Python 的规则下，函数内后出现的赋值
        # 也算局部变量，所以这样不会把合法的前向引用误报成未定义。
        for stmt in ast.walk(node):
            bound_names(stmt, names)
        self.stack.append(names)
        for stmt in node.body:
            self.visit(stmt)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        # 装饰器与默认值在外层作用域求值，先按外层检查
        for dec in node.decorator_list:
            self.visit(dec)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        self._visit_scope(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._visit_scope(node)

    def visit_Lambda(self, node):
        # lambda 的 body 是表达式，不能套用按语句列表遍历的 _visit_scope
        names = set()
        for sub in ast.walk(node):
            bound_names(sub, names)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        self.stack.append(names)
        self.visit(node.body)
        self.stack.pop()

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self._record(node.id, node)


def check_file(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_names = set()
    for stmt in tree.body:
        bound_names(stmt, module_names)
    checker = ScopeChecker(module_names)
    checker.stack.append(module_names)
    for stmt in tree.body:
        checker.visit(stmt)
    return checker.problems


def collect(paths):
    files = []
    for raw in paths:
        target = Path(raw)
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        elif target.exists():
            files.append(target)
        else:
            print(f"跳过不存在的路径：{raw}", file=sys.stderr)
    return [f for f in files if "__pycache__" not in f.parts]


def main(argv):
    if not argv:
        argv = ["core", "ui", "main.py"]
    files = collect(argv)

    total = 0
    for path in files:
        for lineno, name in check_file(path):
            total += 1
            print(f"{path.as_posix()}:{lineno}: 未定义的名字 {name!r}")

    print(f"扫描 {len(files)} 个文件，发现 {total} 处未定义名")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))