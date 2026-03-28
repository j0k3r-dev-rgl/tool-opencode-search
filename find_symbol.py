#!/usr/bin/env python3
"""
find_symbol.py — Locate where a symbol is defined in the workspace.

Given a name (class, interface, function, method), scans the workspace AST
and returns all files + lines where that symbol is defined.

Accepts: <workspace> <name> [options_json]

Options JSON (optional):
  language: string (default "auto") — "auto", "java", "ts"/"typescript"
  kind:     string (default "any")  — filter by kind: "class", "interface",
                                      "function", "method", "type", "any"
  fuzzy:    bool   (default false)  — if true, match names containing <name>
                                      (case-insensitive) instead of exact match
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


# ---------------------------------------------------------------------------
# Node kind constants
# ---------------------------------------------------------------------------

JAVA_DEFINITION_NODES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "annotation_type_declaration": "annotation",
    "method_declaration": "method",
    "interface_method_declaration": "method",
    "constructor_declaration": "constructor",
}

TS_DEFINITION_NODES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "function_declaration": "function",
    "method_definition": "method",
    "abstract_method_signature": "method",
    "enum_declaration": "enum",
}

# TS variable/const that declare functions or components
TS_VARIABLE_NODES = {
    "variable_declarator",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _name_matches(candidate: str, target: str, fuzzy: bool) -> bool:
    if fuzzy:
        return target.lower() in candidate.lower()
    return candidate == target


def _kind_matches(node_kind: str, filter_kind: str) -> bool:
    if filter_kind == "any":
        return True
    return node_kind == filter_kind


# ---------------------------------------------------------------------------
# Java searcher
# ---------------------------------------------------------------------------


def _search_java(
    path: Path,
    workspace: Path,
    name: str,
    kind_filter: str,
    fuzzy: bool,
) -> list[dict]:
    from trace_core.common import node_text, parse, walk

    tree, src = parse(path)
    if not tree:
        return []

    results: list[dict] = []

    for node_type, node_kind in JAVA_DEFINITION_NODES.items():
        if not _kind_matches(node_kind, kind_filter):
            continue
        for node in walk(tree.root_node, node_type):
            # Name is always an "identifier" child
            for child in node.children:
                if child.type == "identifier":
                    candidate = node_text(child, src)
                    if _name_matches(candidate, name, fuzzy):
                        results.append(
                            {
                                "file": str(path.relative_to(workspace)),
                                "line": node.start_point[0] + 1,
                                "kind": node_kind,
                                "name": candidate,
                            }
                        )
                    break

    return results


# ---------------------------------------------------------------------------
# TypeScript/JavaScript searcher
# ---------------------------------------------------------------------------


def _search_ts(
    path: Path,
    workspace: Path,
    name: str,
    kind_filter: str,
    fuzzy: bool,
) -> list[dict]:
    from trace_core.common import node_text, parse, walk

    tree, src = parse(path)
    if not tree:
        return []

    results: list[dict] = []

    # Named declaration nodes (class, interface, type, function, enum)
    for node_type, node_kind in TS_DEFINITION_NODES.items():
        if not _kind_matches(node_kind, kind_filter):
            continue
        for node in walk(tree.root_node, node_type):
            for child in node.children:
                if child.type == "identifier" or child.type == "type_identifier":
                    candidate = node_text(child, src)
                    if _name_matches(candidate, name, fuzzy):
                        results.append(
                            {
                                "file": str(path.relative_to(workspace)),
                                "line": node.start_point[0] + 1,
                                "kind": node_kind,
                                "name": candidate,
                            }
                        )
                    break

    # const/let/var declarations that export functions or components
    if _kind_matches("function", kind_filter) or kind_filter == "any":
        for decl_node in walk(
            tree.root_node, "lexical_declaration", "variable_declaration"
        ):
            for var_decl in walk(decl_node, "variable_declarator"):
                name_node = var_decl.child_by_field_name("name")
                if not name_node:
                    continue
                candidate = node_text(name_node, src)
                if not _name_matches(candidate, name, fuzzy):
                    continue
                # Only include if the value is a function/arrow/component
                value_node = var_decl.child_by_field_name("value")
                if value_node and value_node.type in (
                    "arrow_function",
                    "function_expression",
                    "function",
                    "jsx_element",
                ):
                    results.append(
                        {
                            "file": str(path.relative_to(workspace)),
                            "line": var_decl.start_point[0] + 1,
                            "kind": "function",
                            "name": candidate,
                        }
                    )

    return results


# ---------------------------------------------------------------------------
# Language initializer
# ---------------------------------------------------------------------------


def _init_languages(language: str):
    if language in ("auto", "java"):
        try:
            from trace_core.analyzers import java  # noqa: F401
        except ImportError:
            if language == "java":
                print(json.dumps({"error": "Java tree-sitter parser not available."}))
                sys.exit(1)

    if language in ("auto", "ts", "typescript"):
        try:
            from trace_core.analyzers import typescript  # noqa: F401
        except ImportError:
            if language in ("ts", "typescript"):
                print(
                    json.dumps(
                        {"error": "TypeScript tree-sitter parser not available."}
                    )
                )
                sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 3:
        print(
            json.dumps(
                {"error": "Usage: find_symbol.py <workspace> <name> [options_json]"}
            )
        )
        sys.exit(1)

    workspace_str = sys.argv[1]
    name = sys.argv[2]

    options: dict = {}
    if len(sys.argv) >= 4:
        try:
            options = json.loads(sys.argv[3])
        except json.JSONDecodeError:
            pass

    language = str(options.get("language", "auto")).lower()
    kind_filter = str(options.get("kind", "any")).lower()
    fuzzy = bool(options.get("fuzzy", False))

    _init_languages(language)

    from trace_core.common import SUPPORTED, should_skip

    workspace = Path(workspace_str).resolve()

    # Collect files to scan based on language filter
    ext_map: dict[str, set[str]] = {
        "java": {".java"},
        "ts": {".ts", ".tsx", ".js", ".jsx"},
        "typescript": {".ts", ".tsx", ".js", ".jsx"},
        "auto": set(SUPPORTED.keys()),
    }
    allowed_exts = ext_map.get(language, set(SUPPORTED.keys()))

    matches: list[dict] = []

    for p in sorted(workspace.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in allowed_exts:
            continue
        if should_skip(p):
            continue

        if p.suffix.lower() == ".java":
            found = _search_java(p, workspace, name, kind_filter, fuzzy)
        else:
            found = _search_ts(p, workspace, name, kind_filter, fuzzy)

        matches.extend(found)

    print(
        json.dumps(
            {
                "matches": matches,
                "count": len(matches),
                "options": {
                    "language": language,
                    "kind": kind_filter,
                    "fuzzy": fuzzy,
                },
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(json.dumps({"error": str(exc), "trace": traceback.format_exc()}))
        sys.exit(1)
