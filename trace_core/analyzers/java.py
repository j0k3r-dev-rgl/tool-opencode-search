"""
java.py — Java language analyzer.

Handles: direct method invocations, interface dispatch,
field/local-var type resolution, controller/entry-point annotations.
Supports both reverse trace (analyze_file) and forward trace (extract_references).
"""

from __future__ import annotations

from pathlib import Path

from trace_core.common import (
    SUPPORTED,
    method_name_from_node,
    node_text,
    parse,
    snippet_for_line,
    walk,
)

# ---------------------------------------------------------------------------
# Register Java language on import
# ---------------------------------------------------------------------------

try:
    import tree_sitter_java as tsjava
    from tree_sitter import Language

    JAVA_LANG = Language(tsjava.language())
    SUPPORTED[".java"] = JAVA_LANG
except ImportError:
    JAVA_LANG = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JAVA_SKIP_TYPES = {
    "String",
    "Integer",
    "Long",
    "Double",
    "Float",
    "Boolean",
    "Byte",
    "List",
    "Map",
    "Set",
    "Optional",
    "Collection",
    "ArrayList",
    "HashMap",
    "HashSet",
    "LinkedList",
    "Object",
    "Class",
    "Enum",
    "Record",
    "void",
    "int",
    "long",
    "double",
    "float",
    "boolean",
    "byte",
    "char",
    "LocalDate",
    "LocalDateTime",
    "Instant",
    "Duration",
    "ZonedDateTime",
    "BigDecimal",
    "BigInteger",
    "UUID",
    "MongoTemplate",
    "MongoClient",
    "RedisTemplate",
    "RestTemplate",
    "ResponseEntity",
    "HttpHeaders",
    "HttpStatus",
    "HttpMethod",
    "Authentication",
    "UserDetails",
    "SecurityContext",
    "Builder",
    "Logger",
    "Slf4j",
}

JAVA_CONTROLLER_ANNOTATIONS = {"@RestController", "@Controller"}

JAVA_METHOD_ENTRY_ANNOTATIONS = {
    "@GetMapping",
    "@PostMapping",
    "@PutMapping",
    "@PatchMapping",
    "@DeleteMapping",
    "@QueryMapping",
    "@MutationMapping",
}


# ---------------------------------------------------------------------------
# Java-specific helpers
# ---------------------------------------------------------------------------


def extract_fields_java(tree, src: bytes) -> dict[str, str]:
    fields: dict[str, str] = {}
    for cls in walk(tree.root_node, "class_declaration"):
        for field in walk(cls, "field_declaration"):
            field_type = None
            names: list[str] = []
            for child in field.children:
                if child.type == "type_identifier":
                    field_type = node_text(child, src)
                elif child.type == "generic_type":
                    for sub in child.children:
                        if sub.type == "type_identifier":
                            field_type = node_text(sub, src)
                            break
                elif child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        names.append(node_text(name_node, src))
            if field_type:
                for name in names:
                    fields[name] = field_type
    return fields


def extract_local_vars_java(method_node, src: bytes) -> dict[str, str]:
    locals_map: dict[str, str] = {}
    for decl in walk(method_node, "local_variable_declaration"):
        local_type = None
        names: list[str] = []
        for child in decl.children:
            if child.type == "type_identifier":
                local_type = node_text(child, src)
            elif child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node:
                    names.append(node_text(name_node, src))
        if local_type:
            for name in names:
                locals_map[name] = local_type
    return locals_map


def is_java_interface_file(path: Path) -> bool:
    tree, src = parse(path)
    if not tree or not src:
        return False
    return bool(walk(tree.root_node, "interface_declaration"))


def annotations_before_line(src: bytes, line_number: int) -> list[str]:
    lines = src.decode("utf-8", errors="replace").splitlines()
    idx = max(0, min(line_number - 1, len(lines) - 1))
    if lines and idx < len(lines):
        current = lines[idx].strip()
        if current and not current.startswith("@"):
            idx -= 1
    annotations: list[str] = []
    while idx >= 0:
        stripped = lines[idx].strip()
        if not stripped:
            idx -= 1
            continue
        if stripped.startswith("@"):
            annotations.append(stripped.split("(", 1)[0])
            idx -= 1
            continue
        if (
            stripped.startswith("//")
            or stripped.startswith("/*")
            or stripped.startswith("*")
        ):
            idx -= 1
            continue
        break
    annotations.reverse()
    return annotations


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------


def analyze_file(
    path: Path,
    target_file: Path,
    symbol: str,
    type_index: dict[str, Path],
    target_interfaces: set[str],
    impl_index: dict[str, list[Path]],
) -> list[dict]:
    """Analyze a single Java file for callers of target_file::symbol."""
    tree, src = parse(path)
    if not tree:
        return []

    results: list[dict] = []
    fields = extract_fields_java(tree, src)

    for method_node in walk(
        tree.root_node, "method_declaration", "interface_method_declaration"
    ):
        local_vars = extract_local_vars_java(method_node, src)
        all_vars = {**fields, **local_vars}
        caller_name = method_name_from_node(method_node, src)

        for inv in walk(method_node, "method_invocation"):
            children = list(inv.children)
            receiver = None
            called = None
            if len(children) >= 3 and children[1].type == ".":
                receiver = node_text(children[0], src)
                for child in children[2:]:
                    if child.type == "identifier":
                        called = node_text(child, src)
                        break
            else:
                continue

            if called != symbol or not receiver:
                continue

            receiver_type = all_vars.get(receiver)
            if not receiver_type or receiver_type in JAVA_SKIP_TYPES:
                continue

            relation = None
            direct_target = type_index.get(receiver_type)
            if direct_target == target_file:
                relation = "java.direct_call"
            elif receiver_type in target_interfaces:
                impls = impl_index.get(receiver_type, [])
                if target_file in impls:
                    relation = "java.interface_dispatch"

            if not relation:
                continue

            results.append(
                {
                    "file": str(path),
                    "line": inv.start_point[0] + 1,
                    "column": inv.start_point[1] + 1,
                    "caller": caller_name,
                    "relation": relation,
                    "receiverType": receiver_type,
                    "snippet": snippet_for_line(src, inv.start_point[0]),
                }
            )

    return results


# ---------------------------------------------------------------------------
# Forward trace: extract outgoing references from a method
# ---------------------------------------------------------------------------


def extract_references(method_node, tree, src: bytes) -> list[tuple[str, str]]:
    """
    Extract outgoing references from a Java method.
    Returns list of (receiver_type, method_called).
    Special receiver types:
      - "__self__" for same-class calls
      - "__new__" for object creation expressions
    """
    refs: list[tuple[str, str]] = []
    fields = extract_fields_java(tree, src)
    local_vars = extract_local_vars_java(method_node, src)
    all_vars = {**fields, **local_vars}

    for inv in walk(method_node, "method_invocation"):
        children = list(inv.children)
        if len(children) >= 3 and children[1].type == ".":
            receiver = node_text(children[0], src)
            method_called = None
            for child in children[2:]:
                if child.type == "identifier":
                    method_called = node_text(child, src)
                    break
            receiver_type = all_vars.get(receiver)
            if receiver_type and method_called:
                refs.append((receiver_type, method_called))
        else:
            method_name = None
            for child in children:
                if child.type == "identifier":
                    method_name = node_text(child, src)
                    break
            if method_name:
                refs.append(("__self__", method_name))

    for creation in walk(method_node, "object_creation_expression"):
        for child in creation.children:
            if child.type == "type_identifier":
                refs.append(("__new__", node_text(child, src)))
                break

    return refs
