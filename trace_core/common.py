"""
common.py — Shared utilities for trace_callers.

Parsing, AST walking, workspace helpers, snippet extraction,
enclosing symbol inference, and file discovery.
"""

from __future__ import annotations

import re
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

# ---------------------------------------------------------------------------
# Language registry — populated lazily by each analyzer on import
# ---------------------------------------------------------------------------

SUPPORTED: dict[str, object] = {}  # ext -> Language, filled by analyzers

SKIP_DIRS = {
    "node_modules",
    ".git",
    "target",
    "build",
    "dist",
    ".next",
    "__pycache__",
    ".gradle",
    ".idea",
    "out",
    ".cache",
    ".ruff_cache",
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def node_text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def walk(root: Node, *types: str) -> list[Node]:
    results: list[Node] = []
    q: deque[Node] = deque([root])
    while q:
        node = q.popleft()
        if node.type in types:
            results.append(node)
        q.extend(node.children)
    return results


def child_has_field(node: Node, field_name: str, child: Node) -> bool:
    field_child = node.child_by_field_name(field_name)
    return field_child is not None and field_child.id == child.id


def method_name_from_node(node: Node, src: bytes) -> str:
    for child in node.children:
        if child.type in ("identifier", "property_identifier"):
            return node_text(child, src)
    return "<anonymous>"


def infer_function_like_name(node: Node, src: bytes) -> str:
    current = node
    while current is not None:
        if current.type == "variable_declarator":
            name_node = current.child_by_field_name("name")
            if name_node:
                return node_text(name_node, src)
        if current.type == "pair":
            key_node = current.child_by_field_name("key")
            if key_node:
                return node_text(key_node, src)
        if current.type == "assignment_expression":
            left_node = current.child_by_field_name("left")
            if left_node:
                return node_text(left_node, src)
        current = current.parent
    return "<anonymous>"


def enclosing_symbol_name(node: Node, src: bytes) -> str:
    return _enclosing_symbol(node, src, allow_variable_declarator=True)


def enclosing_callable_name(node: Node, src: bytes) -> str:
    return _enclosing_symbol(node, src, allow_variable_declarator=False)


def _enclosing_symbol(node: Node, src: bytes, allow_variable_declarator: bool) -> str:
    current = node
    while current is not None:
        if current.type in (
            "method_declaration",
            "interface_method_declaration",
            "function_declaration",
            "method_definition",
            "generator_function_declaration",
            "arrow_function",
            "function_expression",
        ):
            name = method_name_from_node(current, src)
            if name == "<anonymous>" and current.type in (
                "arrow_function",
                "function_expression",
            ):
                name = infer_function_like_name(current, src)
            if name != "<anonymous>":
                return name
        if allow_variable_declarator and current.type == "variable_declarator":
            name_node = current.child_by_field_name("name")
            if name_node and name_node.type in ("identifier", "property_identifier"):
                return node_text(name_node, src)
        current = current.parent
    return "<module>"


# ---------------------------------------------------------------------------
# Parsing with cache
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4096)
def _parse_cached(path_str: str):
    path = Path(path_str)
    lang = SUPPORTED.get(path.suffix.lower())
    if not lang:
        return None, None
    try:
        from tree_sitter import Parser

        src = path.read_bytes()
        return Parser(lang).parse(src), src
    except Exception:
        return None, None


def parse(path: Path):
    lang = SUPPORTED.get(path.suffix.lower())
    if not lang:
        return None, None
    return _parse_cached(str(path.resolve()))


# ---------------------------------------------------------------------------
# Workspace & file discovery
# ---------------------------------------------------------------------------


def is_in_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
        return True
    except ValueError:
        return False


def should_skip(path: Path) -> bool:
    return any(skip in path.parts for skip in SKIP_DIRS)


@lru_cache(maxsize=32)
def _supported_files_cached(workspace_str: str) -> tuple[str, ...]:
    workspace = Path(workspace_str)
    files = []
    for p in workspace.rglob("*"):
        if p.suffix.lower() not in SUPPORTED or should_skip(p):
            continue
        files.append(str(p.resolve()))
    return tuple(sorted(files))


def supported_files(workspace: Path, language: str | None = None) -> list[Path]:
    """Return workspace files filtered by language if given."""
    all_files = [Path(p) for p in _supported_files_cached(str(workspace.resolve()))]
    if language is None:
        return all_files

    ext_map = {
        "java": {".java"},
        "ts": {".ts", ".tsx", ".js", ".jsx"},
        "typescript": {".ts", ".tsx", ".js", ".jsx"},
    }
    allowed = ext_map.get(language)
    if allowed is None:
        return all_files
    return [p for p in all_files if p.suffix.lower() in allowed]


def relative_to_workspace(path: Path, workspace: Path) -> str:
    return str(path.resolve().relative_to(workspace))


def snippet_for_line(src: bytes, row: int) -> str:
    lines = src.decode("utf-8", errors="replace").splitlines()
    if row < 0 or row >= len(lines):
        return ""
    return lines[row].strip()


# ---------------------------------------------------------------------------
# Shared symbol/method finding (dispatches by extension)
# ---------------------------------------------------------------------------


def find_method(path: Path, tree, src: bytes, symbol: str):
    """Find a method/function node in a parsed file. Returns Node or None."""
    ext = path.suffix.lower()
    if ext == ".java":
        for node in walk(
            tree.root_node, "method_declaration", "interface_method_declaration"
        ):
            for child in node.children:
                if child.type == "identifier" and node_text(child, src) == symbol:
                    return node
    else:
        for export in walk(tree.root_node, "export_statement"):
            for fn in walk(export, "function_declaration"):
                for child in fn.children:
                    if child.type == "identifier" and node_text(child, src) == symbol:
                        return fn
        for node in walk(tree.root_node, "function_declaration", "method_definition"):
            for child in node.children:
                if (
                    child.type in ("identifier", "property_identifier")
                    and node_text(child, src) == symbol
                ):
                    return node
        for node in walk(tree.root_node, "lexical_declaration", "variable_declaration"):
            for decl in walk(node, "variable_declarator"):
                name_node = decl.child_by_field_name("name")
                if name_node and node_text(name_node, src) == symbol:
                    return decl
    return None


def extract_declared_types(path: Path, tree, src: bytes) -> set[str]:
    declared: set[str] = set()
    if path.suffix.lower() == ".java":
        nodes = walk(
            tree.root_node,
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
        )
    else:
        nodes = walk(
            tree.root_node,
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
        )
    for node in nodes:
        for child in node.children:
            if child.type == "identifier":
                declared.add(node_text(child, src))
                break
    return declared


def extract_implemented_interfaces(tree, src: bytes) -> set[str]:
    interfaces: set[str] = set()
    for cls in walk(tree.root_node, "class_declaration"):
        for child in cls.children:
            if child.type == "super_interfaces":
                for iface in walk(child, "type_identifier"):
                    interfaces.add(node_text(iface, src))
    return interfaces


# ---------------------------------------------------------------------------
# Index builders (used by Java analyzer, cached globally)
# ---------------------------------------------------------------------------


def build_type_index(workspace: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for p in supported_files(workspace):
        tree, src = parse(p)
        if not tree:
            continue
        if p.suffix.lower() == ".java":
            nodes = walk(
                tree.root_node,
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
            )
            for node in nodes:
                for child in node.children:
                    if child.type == "identifier":
                        name = node_text(child, src)
                        if name not in index or node.type == "class_declaration":
                            index[name] = p
                        break
        else:
            nodes = walk(
                tree.root_node,
                "function_declaration",
                "class_declaration",
                "interface_declaration",
                "type_alias_declaration",
            )
            for node in nodes:
                for child in node.children:
                    if child.type == "identifier":
                        name = node_text(child, src)
                        if name not in index:
                            index[name] = p
                        break
            for node in walk(tree.root_node, "export_statement"):
                for decl in walk(node, "variable_declarator"):
                    name_node = decl.child_by_field_name("name")
                    if name_node:
                        name = node_text(name_node, src)
                        if name not in index:
                            index[name] = p
    return index


@lru_cache(maxsize=16)
def _build_type_index_cached(workspace_str: str) -> dict[str, str]:
    workspace = Path(workspace_str)
    return {name: str(p) for name, p in build_type_index(workspace).items()}


def cached_type_index(workspace: Path) -> dict[str, Path]:
    return {
        name: Path(p)
        for name, p in _build_type_index_cached(str(workspace.resolve())).items()
    }


def build_impl_index(workspace: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for p in supported_files(workspace):
        if p.suffix.lower() != ".java":
            continue
        tree, src = parse(p)
        if not tree:
            continue
        for cls in walk(tree.root_node, "class_declaration"):
            for child in cls.children:
                if child.type == "super_interfaces":
                    for iface in walk(child, "type_identifier"):
                        name = node_text(iface, src)
                        index.setdefault(name, []).append(p)
    return index


@lru_cache(maxsize=16)
def _build_impl_index_cached(workspace_str: str) -> dict[str, tuple[str, ...]]:
    workspace = Path(workspace_str)
    return {
        name: tuple(str(p) for p in paths)
        for name, paths in build_impl_index(workspace).items()
    }


def cached_impl_index(workspace: Path) -> dict[str, list[Path]]:
    return {
        name: [Path(p) for p in paths]
        for name, paths in _build_impl_index_cached(str(workspace.resolve())).items()
    }


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------


def make_node_key(file_path: str, symbol: str) -> str:
    return f"{file_path}::{symbol}"


def unique_by_key(items: list[dict], key_fn) -> list[dict]:
    deduped: list[dict] = []
    seen: set = set()
    for item in items:
        key = key_fn(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def unique_callers(callers: list[dict]) -> list[dict]:
    return unique_by_key(
        callers,
        lambda c: (c["file"], c["line"], c["column"], c["caller"], c["relation"]),
    )
