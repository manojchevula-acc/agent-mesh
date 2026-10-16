"""WP1-CTX-02 step 3: no hand-typed ``gernas.*`` attribute names outside gernas/attributes.py.

Every decorator must use ``Ctx.ATTRIBUTE_NAME``. Docstrings and prose are ignored;
any identifier-like string literal ``gernas.<something>`` fails the build.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "gernas"
_PATTERN = re.compile(r"^gernas\.[a-z0-9_]+(\.[a-z0-9_]+)*$")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                ids.add(id(body[0].value))
    return ids


def find_handtyped(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if _PATTERN.match(node.value):
                hits.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            head = node.values[0] if node.values else None
            if isinstance(head, ast.Constant) and isinstance(head.value, str) and head.value.startswith("gernas."):
                hits.append((node.lineno, head.value + "{...}"))
    return hits


def test_no_handtyped_gernas_attribute_names():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "attributes.py" and path.parent == PACKAGE:
            continue
        for line, value in find_handtyped(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(PACKAGE.parent)}:{line}: {value!r}")
    assert not offenders, "use gernas.Ctx constants instead:\n" + "\n".join(offenders)


def test_the_lint_catches_an_injected_violation():
    snippet = 'def f(span):\n    """mentions gernas.agent_id in prose: fine"""\n    span.set_attribute("gernas.agent_id", 1)\n'
    assert find_handtyped(snippet) == [(3, "gernas.agent_id")]
    assert find_handtyped('x = f"gernas.{k}"') == [(1, "gernas.{...}")]
