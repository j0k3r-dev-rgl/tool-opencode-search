"""
java.py — Spring Boot endpoint analyzer.

Detects:
  - @QueryMapping  → GraphQL query
  - @MutationMapping → GraphQL mutation
  - @GetMapping    → REST GET
  - @PostMapping   → REST POST
  - @PutMapping    → REST PUT
  - @PatchMapping  → REST PATCH
  - @DeleteMapping → REST DELETE
  - @RequestMapping (class level) → base path for the controller

Only scans classes annotated with @RestController, @Controller, or @GraphQL-style.
Uses tree-sitter for real AST parsing — no regex.
"""

from __future__ import annotations

from pathlib import Path

from endpoints_core.common import (
    SKIP_DIRS,
    AnalyzerResult,
    Endpoint,
    register,
    relative,
)

# ---------------------------------------------------------------------------
# Annotation → kind mapping
# ---------------------------------------------------------------------------

METHOD_ANNOTATIONS: dict[str, str] = {
    "@QueryMapping": "query",
    "@MutationMapping": "mutation",
    "@GetMapping": "get",
    "@PostMapping": "post",
    "@PutMapping": "put",
    "@PatchMapping": "patch",
    "@DeleteMapping": "delete",
    "@RequestMapping": "request",
}

CONTROLLER_ANNOTATIONS = {"@RestController", "@Controller"}


# ---------------------------------------------------------------------------
# Tree-sitter helpers
# ---------------------------------------------------------------------------


def _node_text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _walk(root, *types):
    from collections import deque

    results = []
    q = deque([root])
    while q:
        node = q.popleft()
        if node.type in types:
            results.append(node)
        q.extend(node.children)
    return results


def _annotation_name(node, src: bytes) -> str:
    """Extract '@AnnotationName' from a marker_annotation or normal_annotation node."""
    for child in node.children:
        if child.type == "identifier":
            return "@" + _node_text(child, src)
    return ""


def _annotation_path_value(node, src: bytes) -> str | None:
    """
    Extract the path string from @GetMapping("/path") or @RequestMapping(value="/path").
    Returns None if no string literal is found.
    """
    for child in node.children:
        # normal_annotation: @GetMapping(value = "/path") or @GetMapping("/path")
        if child.type == "annotation_argument_list":
            # look for string_literal directly or in element_value_pair
            for arg in _walk(child, "string_literal"):
                val = _node_text(arg, src).strip('"')
                return val
    return None


def _annotation_nodes(node, src: bytes):
    """Yield annotation nodes from a class or method node (inside modifiers or direct)."""
    for child in node.children:
        if child.type == "modifiers":
            for sub in child.children:
                if sub.type in ("marker_annotation", "normal_annotation"):
                    yield sub
        elif child.type in ("marker_annotation", "normal_annotation"):
            yield child


def _class_annotations(class_node, src: bytes) -> list[str]:
    """Collect annotation names on a class_declaration node."""
    return [_annotation_name(a, src) for a in _annotation_nodes(class_node, src)]


def _class_base_path(class_node, src: bytes) -> str:
    """Extract base path from @RequestMapping on the class, if any."""
    for ann in _annotation_nodes(class_node, src):
        name = _annotation_name(ann, src)
        if name == "@RequestMapping":
            path = _annotation_path_value(ann, src)
            if path:
                return path.rstrip("/")
    return ""


def _class_name(class_node, src: bytes) -> str:
    for child in class_node.children:
        if child.type == "identifier":
            return _node_text(child, src)
    return "<unknown>"


def _method_name(method_node, src: bytes) -> str:
    for child in method_node.children:
        if child.type == "identifier":
            return _node_text(child, src)
    return "<unknown>"


# ---------------------------------------------------------------------------
# File analyzer
# ---------------------------------------------------------------------------


def _analyze_file(path: Path, workspace: Path, src: bytes, tree) -> list[Endpoint]:
    endpoints: list[Endpoint] = []

    for class_node in _walk(tree.root_node, "class_declaration"):
        class_annots = _class_annotations(class_node, src)

        # Only process controller classes
        if not any(a in CONTROLLER_ANNOTATIONS for a in class_annots):
            continue

        class_name = _class_name(class_node, src)
        base_path = _class_base_path(class_node, src)
        rel = relative(path, workspace)

        for method_node in _walk(class_node, "method_declaration"):
            # Collect annotations on this method (inside modifiers or direct)
            method_annots: list[tuple[str, str | None]] = []  # (name, path_value)
            for ann in _annotation_nodes(method_node, src):
                ann_name = _annotation_name(ann, src)
                if ann_name in METHOD_ANNOTATIONS:
                    ann_path = _annotation_path_value(ann, src)
                    method_annots.append((ann_name, ann_path))

            if not method_annots:
                continue

            method_nm = _method_name(method_node, src)
            line = method_node.start_point[0] + 1

            for ann_name, ann_path in method_annots:
                kind = METHOD_ANNOTATIONS[ann_name]

                # Build full HTTP path for REST endpoints
                full_path: str | None = None
                if kind not in ("query", "mutation"):
                    parts = [base_path, ann_path or ""]
                    full_path = "/".join(p.strip("/") for p in parts if p)
                    full_path = "/" + full_path if full_path else None

                endpoints.append(
                    Endpoint(
                        name=method_nm,
                        kind=kind,
                        file=rel,
                        line=line,
                        path=full_path,
                        group=class_name,
                    )
                )

    return endpoints


# ---------------------------------------------------------------------------
# Public analyze function — registered below
# ---------------------------------------------------------------------------


def analyze(workspace: Path, options: dict) -> AnalyzerResult:
    try:
        import tree_sitter_java as tsjava
        from tree_sitter import Language, Parser

        JAVA_LANG = Language(tsjava.language())
    except ImportError:
        return AnalyzerResult(language="java", framework="spring", endpoints=[])

    result = AnalyzerResult(language="java", framework="spring")

    for p in sorted(workspace.rglob("*.java")):
        if any(skip in p.parts for skip in SKIP_DIRS):
            continue
        try:
            src = p.read_bytes()
            tree = Parser(JAVA_LANG).parse(src)
            result.endpoints.extend(_analyze_file(p, workspace, src, tree))
        except Exception:
            continue

    return result


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

register("java", analyze)
