import ast
import pathlib
import sys

bad: list[str] = []
for path in pathlib.Path("src").rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes: list[ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = [
        tree
    ]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nodes.append(node)
    for node in nodes:
        if ast.get_docstring(node):
            bad.append(f"{path}:{getattr(node, 'lineno', 1)}")

if bad:
    print("docstrings found:\n" + "\n".join(bad))
    sys.exit(1)
