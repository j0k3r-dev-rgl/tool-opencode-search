"""
typescript.py — React Router 7 route analyzer.

Detects routes by scanning files under any 'routes/' directory.
For each route file it checks which of these are exported:
  - loader   → kind "loader"
  - action   → kind "action"
  - default  → kind "component" (only included if includeComponents=true in options)

Files under routes/api/ are classified as "resource" routes.
Layout files (named 'layout.tsx' or prefixed with '_') are marked as layouts
and excluded from URL path inference.

Route path is inferred from the file path relative to the routes/ folder,
following React Router 7 file-based routing conventions:
  routes/titular/titular.tsx       → /titular/titular
  routes/titular/$id.tsx           → /titular/:id
  routes/api/session.tsx           → /api/session  (resource)
  routes/_layout.tsx               → [layout] (no real URL)
  routes/promotor/layout.tsx       → [layout] (no real URL)
"""

from __future__ import annotations

import re
from pathlib import Path

from endpoints_core.common import (
    SKIP_DIRS,
    AnalyzerResult,
    Endpoint,
    register,
    relative,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROUTE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}

# React Router 7 path segment conventions
_DYNAMIC_RE = re.compile(r"^\$(.+)$")  # $id → :id
_SPLAT_RE = re.compile(r"^\$\$$")  # $$ → *
_LAYOUT_PREFIX_RE = re.compile(r"^_")  # _layout → layout segment (no URL)
_INDEX_RE = re.compile(r"^_?index$", re.I)  # index → ""
_LAYOUT_NAME_RE = re.compile(r"^layout$", re.I)  # layout.tsx → layout file


def _is_layout_file(file: Path) -> bool:
    """Return True if this file is a layout (by name or underscore prefix convention)."""
    stem = file.stem
    return bool(_LAYOUT_NAME_RE.match(stem) or _LAYOUT_PREFIX_RE.match(stem))


def _segment_to_path(segment: str) -> str | None:
    """
    Convert a filename/dir segment to its URL equivalent.
    Returns None for layout segments (excluded from URL).
    """
    stem = Path(segment).stem  # strip extension if present
    if _LAYOUT_PREFIX_RE.match(stem) or _LAYOUT_NAME_RE.match(stem):
        return None  # layout — not a real URL segment
    if _INDEX_RE.match(stem):
        return ""  # index route
    if _SPLAT_RE.match(stem):
        return "*"
    m = _DYNAMIC_RE.match(stem)
    if m:
        return f":{m.group(1)}"
    return stem


def _infer_route_path(file: Path, routes_root: Path) -> str:
    """Infer the URL path from the file's position under the routes root."""
    try:
        rel = file.relative_to(routes_root)
    except ValueError:
        return "/" + file.stem

    segments: list[str] = []
    for part in rel.parts:
        converted = _segment_to_path(part)
        if converted is None:
            continue  # layout segment — skip
        if converted:
            segments.append(converted)

    return "/" + "/".join(segments) if segments else "/"


def _find_routes_root(workspace: Path) -> Path | None:
    """Find the first 'routes' directory under the workspace, skipping generated dirs."""
    for p in sorted(workspace.rglob("routes")):
        if p.is_dir() and not any(skip in p.parts for skip in SKIP_DIRS):
            return p
    return None


# ---------------------------------------------------------------------------
# Export detection — text-based (no tree-sitter needed for simple exports)
# ---------------------------------------------------------------------------

_EXPORT_LOADER_RE = re.compile(r"\bexport\s+(?:async\s+)?function\s+loader\b")
_EXPORT_ACTION_RE = re.compile(r"\bexport\s+(?:async\s+)?function\s+action\b")
_EXPORT_DEFAULT_RE = re.compile(r"\bexport\s+default\b")
_EXPORT_LOADER_CONST_RE = re.compile(r"\bexport\s+const\s+loader\b")
_EXPORT_ACTION_CONST_RE = re.compile(r"\bexport\s+const\s+action\b")


def _detect_exports(content: str) -> dict[str, int]:
    """
    Return a dict of {export_kind: line_number} for loader, action, default.
    Line numbers are 1-indexed.
    """
    found: dict[str, int] = {}
    lines = content.splitlines()
    for i, line in enumerate(lines, start=1):
        if "loader" not in found and (
            _EXPORT_LOADER_RE.search(line) or _EXPORT_LOADER_CONST_RE.search(line)
        ):
            found["loader"] = i
        if "action" not in found and (
            _EXPORT_ACTION_RE.search(line) or _EXPORT_ACTION_CONST_RE.search(line)
        ):
            found["action"] = i
        if "default" not in found and _EXPORT_DEFAULT_RE.search(line):
            found["default"] = i
    return found


# ---------------------------------------------------------------------------
# Public analyze function
# ---------------------------------------------------------------------------


def analyze(workspace: Path, options: dict) -> AnalyzerResult:
    include_components = bool(options.get("includeComponents", False))
    result = AnalyzerResult(language="typescript", framework="react-router-7")

    routes_root = _find_routes_root(workspace)
    if not routes_root:
        return result

    for p in sorted(routes_root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ROUTE_EXTENSIONS:
            continue
        if any(skip in p.parts for skip in SKIP_DIRS):
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        exports = _detect_exports(content)
        if not exports:
            continue

        is_layout = _is_layout_file(p)
        route_path = "[layout]" if is_layout else _infer_route_path(p, routes_root)
        rel = relative(p, workspace)
        group = p.stem

        # Classify as resource route if under routes/api/
        try:
            p.relative_to(routes_root / "api")
            is_resource = True
        except ValueError:
            is_resource = False

        for export_kind, line in exports.items():
            # Skip bare default when loader/action already captured the route
            if export_kind == "default" and (
                "loader" in exports or "action" in exports
            ):
                continue

            # Determine kind
            if is_layout:
                kind = "layout"
            elif is_resource:
                kind = "resource"
            elif export_kind == "loader":
                kind = "loader"
            elif export_kind == "action":
                kind = "action"
            else:
                kind = "component"

            # Skip component-only pages unless explicitly requested
            if kind == "component" and not include_components:
                continue

            result.endpoints.append(
                Endpoint(
                    name=export_kind,
                    kind=kind,
                    file=rel,
                    line=line,
                    path=route_path,
                    group=group,
                )
            )

    return result


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

register("typescript", analyze)
