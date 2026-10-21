"""Architecture invariants. A failure indicates a layer rule violation (see memory/02-architecture.md).

Checks use static AST analysis and do not require a browser.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "aliweb"

#: Allowed dependencies between layers (which aliweb packages may be imported).
ALLOWED: dict[str, set[str]] = {
    "domain": set(),
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
    "aliexpress": {"domain", "application", "infrastructure"},
    "cli": {"domain", "application", "infrastructure", "aliexpress", "bootstrap", "api"},
    "api": {"domain", "application"},
    "bootstrap": {"domain", "application", "infrastructure", "aliexpress", "api"},
    "__main__": {"cli"},
}
#: Locations allowed to import patchright (browser-dependent code).
BROWSER_AWARE = ("infrastructure/browser", "infrastructure/diagnostics", "aliexpress")
#: Standard library modules allowed in the domain (pure computation, no I/O).
DOMAIN_STDLIB = {
    "__future__", "collections", "dataclasses", "datetime", "decimal", "enum", "hmac",
    "re", "typing", "urllib",
}  # fmt: skip


def modules() -> list[tuple[Path, str]]:
    result = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        layer = rel.parts[0] if len(rel.parts) > 1 else rel.stem
        result.append((path, layer))
    return result


def imports_of(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.level == 0, f"{path}: relative imports are forbidden"
            found.append((node.module, node.lineno))
    return found


def rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


@pytest.mark.parametrize(("path", "layer"), modules(), ids=lambda v: rel(v) if isinstance(v, Path) else v)
def test_layer_dependencies(path: Path, layer: str) -> None:
    if layer == "__init__":
        return
    allowed = ALLOWED[layer] | {layer}
    for module, line in imports_of(path):
        parts = module.split(".")
        if parts[0] != "aliweb":
            continue
        target = parts[1] if len(parts) > 1 else "__init__"
        assert target in allowed, f"{rel(path)}:{line} imports aliweb.{target}; layer {layer!r} may not"


def test_patchright_only_in_browser_aware_code() -> None:
    for path, _ in modules():
        for module, line in imports_of(path):
            if module.split(".")[0] == "patchright":
                assert rel(path).startswith(BROWSER_AWARE), (
                    f"{rel(path)}:{line}: patchright imported outside the browser layer"
                )


def test_domain_is_pure_stdlib_only() -> None:
    for path, layer in modules():
        if layer != "domain":
            continue
        for module, line in imports_of(path):
            root = module.split(".")[0]
            assert root == "aliweb" or root in DOMAIN_STDLIB, (
                f"{rel(path)}:{line}: import {module} is forbidden in the domain"
            )


def test_application_has_no_third_party_or_io_dependencies() -> None:
    forbidden = {"patchright", "cryptography", "dotenv", "os", "pathlib", "sys", "tempfile", "shutil"}
    for path, layer in modules():
        if layer != "application":
            continue
        for module, line in imports_of(path):
            assert module.split(".")[0] not in forbidden, (
                f"{rel(path)}:{line}: application must not import {module}"
            )


def test_environ_only_read_in_config() -> None:
    # CLI reads only explicitly named password variables.
    allowed = {"infrastructure/config.py", "cli/main.py"}
    for path, _ in modules():
        if rel(path) in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        assert "os.environ" not in source and "getenv" not in source, f"{rel(path)}: env read outside config"


def test_print_only_in_cli() -> None:
    for path, layer in modules():
        if layer == "cli":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                pytest.fail(f"{rel(path)}:{node.lineno}: print() outside CLI; use logging")


def test_concrete_infrastructure_wired_only_in_bootstrap() -> None:
    """application, aliexpress, and cli must not construct infrastructure; bootstrap owns that task."""
    for path, layer in modules():
        if layer not in {"application", "domain"}:
            continue
        for module, line in imports_of(path):
            assert not module.startswith("aliweb.infrastructure"), f"{rel(path)}:{line}"


def test_every_package_has_init() -> None:
    for directory in {p.parent for p, _ in modules()}:
        assert (directory / "__init__.py").exists(), f"{directory} is missing __init__.py"


def test_browserforge_stealth_only_in_the_fingerprint_module() -> None:
    """Only the fingerprint adapter may import the fingerprint generation package."""
    for path, _ in modules():
        for module, line in imports_of(path):
            if module.split(".")[0] in {"browserforge", "browserforge_stealth"}:
                assert rel(path) == "infrastructure/browser/fingerprint.py", f"{rel(path)}:{line}"
