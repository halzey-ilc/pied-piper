"""Architecture tests (acceptance item 8).

1. AST scan: domain/application layers never import frameworks or outer layers.
2. Import graph (grimp): no import cycles between modules of our packages.
3. import-linter contracts from pyproject.toml are kept.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import grimp
import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

FRAMEWORKS = (
    "django",
    "rest_framework",
    "rest_framework_simplejwt",
    "celery",
    "kombu",
    "redis",
    "psycopg",
    "httpx",
    "opentelemetry",
    "sentry_sdk",
    "prometheus_client",
    "pydantic_settings",
)
OUTER_LAYER_PARTS = ("infrastructure", "presentation")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def _layer_files(layer: str) -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if f"/{layer}/" in p.as_posix())


def _violations(layer: str, forbidden_external: tuple[str, ...]) -> list[str]:
    problems = []
    for path in _layer_files(layer):
        for name in _imports(path):
            top = name.split(".")[0]
            outer = top in ("entrypoints", "config") or any(
                part in OUTER_LAYER_PARTS for part in name.split(".")
            )
            if top in forbidden_external or outer:
                problems.append(f"{path.relative_to(ROOT)} imports {name}")
            if layer == "domain" and ("application" in name.split(".") or top == "pydantic"):
                problems.append(f"{path.relative_to(ROOT)} imports {name}")
    return problems


def test_layer_files_exist() -> None:
    assert _layer_files("domain") and _layer_files("application")


def test_domain_layer_is_pure() -> None:
    assert _violations("domain", FRAMEWORKS) == []


def test_application_layer_does_not_import_infrastructure() -> None:
    assert _violations("application", FRAMEWORKS) == []


def _find_cycles(graph: grimp.ImportGraph, modules: set[str]) -> list[list[str]]:
    """Tarjan's strongly connected components on the module-level import graph."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in graph.find_modules_directly_imported_by(node) & modules:
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for module in sorted(modules):
        if module not in index:
            visit(module)
    return cycles


def test_no_import_cycles() -> None:
    packages = ("platform_core", "shared", "modules", "entrypoints")
    graph = grimp.build_graph(*packages, cache_dir=None)
    modules = {m for m in graph.modules if m.split(".")[0] in packages}
    assert _find_cycles(graph, modules) == []


@pytest.mark.skipif(sys.platform == "win32", reason="lint-imports runs in the Linux toolchain")
def test_import_linter_contracts() -> None:
    lint_imports = Path(sys.executable).with_name("lint-imports")
    result = subprocess.run(  # noqa: S603 - fixed executable from our venv
        [str(lint_imports), "--no-cache"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
