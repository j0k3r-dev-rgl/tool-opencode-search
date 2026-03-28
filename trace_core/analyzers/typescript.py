"""
typescript.py — TypeScript/JavaScript/JSX analyzer.

Handles: import resolution (named, default, namespace, alias, ~/),
direct calls, direct non-call references, JSX usage.
Supports both reverse trace (analyze_file) and forward trace (extract_references).
"""

from __future__ import annotations

import re
from pathlib import Path

from trace_core.common import (
    SUPPORTED,
    child_has_field,
    enclosing_callable_name,
    enclosing_symbol_name,
    is_in_workspace,
    node_text,
    parse,
    snippet_for_line,
    walk,
)

# ---------------------------------------------------------------------------
# Register TS/JS languages on import
# ---------------------------------------------------------------------------

try:
    import tree_sitter_typescript as tsts
    from tree_sitter import Language

    TS_LANG = Language(tsts.language_typescript())
    TSX_LANG = Language(tsts.language_tsx())
    SUPPORTED[".ts"] = TS_LANG
    SUPPORTED[".tsx"] = TSX_LANG
    SUPPORTED[".js"] = TS_LANG
    SUPPORTED[".jsx"] = TSX_LANG
except ImportError:
    TS_LANG = None
    TSX_LANG = None


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------

IMPORT_RE = re.compile(
    r"^\s*import\s+(?P<clause>.+?)\s+from\s+[\"'](?P<module>[^\"']+)[\"']\s*;?\s*$",
    re.DOTALL,
)


def resolve_ts_path(module_path: str, from_file: Path, workspace: Path) -> Path | None:
    if not module_path.startswith(".") and not module_path.startswith("~/"):
        return None

    base = from_file.parent
    normalized = module_path
    if module_path.startswith("~/"):
        normalized = module_path[2:]
        candidate_bases = [
            workspace / "app",
            workspace / "front" / "app",
            workspace / "src",
            workspace / "front" / "src",
            workspace,
        ]
        for candidate_base in candidate_bases:
            if candidate_base.exists():
                candidate = (candidate_base / normalized).resolve()
                if candidate.exists() and is_in_workspace(candidate, workspace):
                    return candidate
                for ext in [".ts", ".tsx", ".js", ".jsx"]:
                    file_candidate = Path(str(candidate) + ext)
                    if file_candidate.exists() and is_in_workspace(
                        file_candidate, workspace
                    ):
                        return file_candidate
                for ext in [".ts", ".tsx", ".js", ".jsx"]:
                    index_candidate = candidate / f"index{ext}"
                    if index_candidate.exists() and is_in_workspace(
                        index_candidate, workspace
                    ):
                        return index_candidate
        return None

    candidate_base = (base / normalized).resolve()
    if candidate_base.exists() and is_in_workspace(candidate_base, workspace):
        return candidate_base

    for ext in [".ts", ".tsx", ".js", ".jsx"]:
        candidate = Path(str(candidate_base) + ext)
        if candidate.exists() and is_in_workspace(candidate, workspace):
            return candidate

    for ext in [".ts", ".tsx", ".js", ".jsx"]:
        candidate = candidate_base / f"index{ext}"
        if candidate.exists() and is_in_workspace(candidate, workspace):
            return candidate

    return None


def parse_import_clause(clause: str) -> list[tuple[str, str, str]]:
    bindings: list[tuple[str, str, str]] = []
    remaining = clause.strip()

    if remaining.startswith("type "):
        remaining = remaining[5:].strip()

    if "{" in remaining and "}" in remaining:
        prefix, _, rest = remaining.partition("{")
        named_part, _, _ = rest.partition("}")
        default_part = prefix.strip().rstrip(",").strip()
        if default_part:
            bindings.append((default_part, "default", "default_import"))
        for raw in named_part.split(","):
            part = raw.strip()
            if not part:
                continue
            if part.startswith("type "):
                part = part[5:].strip()
            if " as " in part:
                imported_name, local_name = [p.strip() for p in part.split(" as ", 1)]
            else:
                imported_name = local_name = part
            bindings.append((local_name, imported_name, "named_import"))
        return bindings

    if remaining.startswith("* as "):
        bindings.append((remaining[5:].strip(), "*", "namespace_import"))
        return bindings

    if remaining:
        bindings.append((remaining, "default", "default_import"))
    return bindings


def extract_import_bindings(
    tree, src: bytes, from_file: Path, workspace: Path
) -> dict[str, dict]:
    bindings: dict[str, dict] = {}
    for node in walk(tree.root_node, "import_statement"):
        statement = node_text(node, src).replace("\n", " ").strip()
        match = IMPORT_RE.match(statement)
        if not match:
            continue

        module_path = match.group("module")
        target_path = resolve_ts_path(module_path, from_file, workspace)
        if not target_path:
            continue

        for local_name, imported_name, kind in parse_import_clause(
            match.group("clause")
        ):
            bindings[local_name] = {
                "resolved_file": target_path,
                "imported_name": imported_name,
                "kind": kind,
            }
    return bindings


def is_ts_symbol_default_exported(tree, src: bytes, symbol: str) -> bool:
    for node in walk(tree.root_node, "export_statement"):
        text = node_text(node, src).strip()
        if re.search(
            rf"export\s+default\s+(async\s+)?function\s+{re.escape(symbol)}\b", text
        ):
            return True
        if re.search(rf"export\s+default\s+class\s+{re.escape(symbol)}\b", text):
            return True
        if re.search(rf"export\s+default\s+{re.escape(symbol)}\s*;?$", text):
            return True
    return False


# ---------------------------------------------------------------------------
# Reference classification helpers
# ---------------------------------------------------------------------------

_TS_TYPE_CONTEXTS = {
    "type_annotation",
    "type_alias_declaration",
    "interface_declaration",
    "extends_type_clause",
    "implements_clause",
    "type_arguments",
    "type_parameters",
    "type_predicate",
    "lookup_type",
    "union_type",
    "intersection_type",
    "array_type",
    "readonly_type",
    "conditional_type",
    "parenthesized_type",
    "function_type",
    "constructor_type",
    "infer_type",
    "mapped_type_clause",
    "template_literal_type",
    "object_type",
    "property_signature",
    "method_signature",
    "index_signature",
    "type_query",
}


def _is_ts_type_context(node) -> bool:
    current = node.parent
    while current is not None:
        if current.type in _TS_TYPE_CONTEXTS:
            return True
        current = current.parent
    return False


def _is_ts_value_reference(node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    if _is_ts_type_context(node):
        return False
    if parent.type in {
        "import_specifier",
        "namespace_import",
        "import_clause",
        "export_specifier",
    }:
        return False
    if parent.type in {
        "function_declaration",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "method_definition",
    } and child_has_field(parent, "name", node):
        return False
    if parent.type in {
        "variable_declarator",
        "required_parameter",
        "optional_parameter",
        "rest_parameter",
        "pair_pattern",
        "object_pattern",
        "array_pattern",
        "assignment_pattern",
    } and child_has_field(parent, "name", node):
        return False
    if parent.type == "pair" and child_has_field(parent, "key", node):
        return False
    if parent.type == "member_expression" and child_has_field(parent, "property", node):
        return False
    return True


def _relation_for_reference(node, binding: dict, via_namespace: bool) -> str | None:
    parent = node.parent
    if parent is None:
        return None
    if parent.type == "call_expression" and child_has_field(parent, "function", node):
        return None
    if parent.type == "new_expression" and child_has_field(parent, "constructor", node):
        return "ts.namespace_new" if via_namespace else "ts.imported_new"
    if not _is_ts_value_reference(node):
        return None
    if via_namespace:
        return "ts.namespace_reference"
    if binding["kind"] == "namespace_import":
        return None
    return "ts.imported_reference"


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------


def analyze_file(
    path: Path,
    workspace: Path,
    target_file: Path,
    symbol: str,
    target_default_exported: bool,
) -> list[dict]:
    """Analyze a single TS/JS file for callers/references of target_file::symbol."""
    tree, src = parse(path)
    if not tree:
        return []

    bindings = extract_import_bindings(tree, src, path, workspace)
    if not bindings:
        return []

    def binding_matches(binding: dict) -> bool:
        if binding["resolved_file"] != target_file:
            return False
        if binding["imported_name"] == symbol:
            return True
        if binding["kind"] == "default_import" and target_default_exported:
            return True
        return False

    results: list[dict] = []
    seen: set[tuple[int, int, str]] = set()

    def append(node, relation: str):
        key = (node.start_point[0], node.start_point[1], relation)
        if key in seen:
            return
        seen.add(key)
        results.append(
            {
                "file": str(path),
                "line": node.start_point[0] + 1,
                "column": node.start_point[1] + 1,
                "caller": enclosing_symbol_name(node, src),
                "traverse_symbol": enclosing_callable_name(node, src),
                "relation": relation,
                "snippet": snippet_for_line(src, node.start_point[0]),
            }
        )

    # Direct calls
    for call in walk(tree.root_node, "call_expression"):
        fn = call.child_by_field_name("function")
        if not fn:
            continue
        relation = None
        if fn.type == "identifier":
            binding = bindings.get(node_text(fn, src))
            if binding and binding_matches(binding):
                relation = "ts.imported_call"
        elif fn.type == "member_expression":
            obj = fn.child_by_field_name("object")
            prop = fn.child_by_field_name("property")
            if obj and prop and obj.type == "identifier":
                binding = bindings.get(node_text(obj, src))
                if (
                    binding
                    and binding["resolved_file"] == target_file
                    and binding["kind"] == "namespace_import"
                    and node_text(prop, src) == symbol
                ):
                    relation = "ts.namespace_call"
        if relation:
            append(call, relation)

    # Direct non-call references (identifiers)
    for node in walk(tree.root_node, "identifier", "shorthand_property_identifier"):
        local_name = node_text(node, src)
        binding = bindings.get(local_name)
        if not binding or not binding_matches(binding):
            continue
        relation = _relation_for_reference(node, binding, via_namespace=False)
        if relation:
            append(node, relation)

    # Namespace member references
    for member in walk(tree.root_node, "member_expression"):
        obj = member.child_by_field_name("object")
        prop = member.child_by_field_name("property")
        if not obj or not prop or obj.type != "identifier":
            continue
        binding = bindings.get(node_text(obj, src))
        if (
            not binding
            or binding["resolved_file"] != target_file
            or binding["kind"] != "namespace_import"
            or node_text(prop, src) != symbol
        ):
            continue
        relation = _relation_for_reference(member, binding, via_namespace=True)
        if relation:
            append(member, relation)

    return results


# ---------------------------------------------------------------------------
# Forward trace: extract outgoing references from a method
# ---------------------------------------------------------------------------


def extract_references(
    method_node, tree, src: bytes, from_file: Path, workspace: Path
) -> list[tuple[Path, str]]:
    """
    Extract outgoing call references from a TS/JS method.
    Returns list of (resolved_file, symbol_name) for each imported function called.
    """
    imports = extract_import_bindings(tree, src, from_file, workspace)
    called: set[str] = set()

    for call in walk(method_node, "call_expression"):
        fn = call.child_by_field_name("function")
        if not fn:
            continue
        if fn.type == "identifier":
            called.add(node_text(fn, src))
        elif fn.type == "member_expression":
            prop = fn.child_by_field_name("property")
            if prop:
                called.add(node_text(prop, src))

    resolved: list[tuple[Path, str]] = []
    for sym in called:
        binding = imports.get(sym)
        if not binding:
            continue
        target = binding["resolved_file"]
        imported_name = binding["imported_name"]
        # Use the imported name if it's a named import, otherwise use the local name
        actual_symbol = imported_name if imported_name not in ("default", "*") else sym
        resolved.append((target, actual_symbol))

    return resolved
