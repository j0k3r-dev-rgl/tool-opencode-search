"""
formatter.py — Compact text output for list_endpoints results.

Groups endpoints by language → kind → group (controller / route file).
Designed to be token-efficient: file + line + kind per line, no content.
"""

from __future__ import annotations

from endpoints_core.common import AnalyzerResult, Endpoint

# ---------------------------------------------------------------------------
# Kind display config
# ---------------------------------------------------------------------------

KIND_ORDER = [
    "query",
    "mutation",
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "request",
    "loader",
    "action",
    "resource",
    "layout",
    "component",
]

KIND_LABELS: dict[str, str] = {
    "query": "GraphQL Queries",
    "mutation": "GraphQL Mutations",
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "request": "RequestMapping",
    "loader": "Loaders",
    "action": "Actions",
    "resource": "Resource Routes",
    "layout": "Layouts",
    "component": "Pages (component only)",
}

FRAMEWORK_LABELS: dict[str, str] = {
    "spring": "Java / Spring Boot",
    "react-router-7": "TypeScript / React Router 7",
}


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------


def _group_by_kind(endpoints: list[Endpoint]) -> dict[str, list[Endpoint]]:
    groups: dict[str, list[Endpoint]] = {}
    for ep in endpoints:
        groups.setdefault(ep.kind, []).append(ep)
    return groups


def _col_widths(endpoints: list[Endpoint]) -> tuple[int, int]:
    """Return (max name width, max path width) for alignment."""
    name_w = max((len(ep.name) for ep in endpoints), default=20)
    path_w = max((len(ep.path or "") for ep in endpoints), default=0)
    return min(name_w, 40), min(path_w, 40)


# ---------------------------------------------------------------------------
# Public formatter
# ---------------------------------------------------------------------------


def format_results(results: list[AnalyzerResult], type_filter: str) -> str:
    lines: list[str] = []

    total = sum(len(r.endpoints) for r in results)
    if total == 0:
        return "No endpoints found."

    # Summary header
    lines.append(f"Endpoints found: {total}")
    lines.append("")

    for result in results:
        if not result.endpoints:
            continue

        # Apply type filter
        endpoints = result.endpoints
        if type_filter != "any":
            endpoints = _filter_by_type(endpoints, type_filter)
        if not endpoints:
            continue

        framework_label = FRAMEWORK_LABELS.get(result.framework, result.framework)
        lines.append(f"── {framework_label} ──────────────────────────")
        lines.append("")

        by_kind = _group_by_kind(endpoints)
        name_w, path_w = _col_widths(endpoints)

        for kind in KIND_ORDER:
            if kind not in by_kind:
                continue
            kind_eps = by_kind[kind]
            label = KIND_LABELS.get(kind, kind.upper())
            lines.append(f"  {label} ({len(kind_eps)})")

            # Group by controller/file within this kind
            by_group: dict[str, list[Endpoint]] = {}
            for ep in kind_eps:
                by_group.setdefault(ep.group, []).append(ep)

            for group_name, group_eps in sorted(by_group.items()):
                lines.append(f"    {group_name}")
                for ep in group_eps:
                    name_col = ep.name.ljust(name_w)
                    path_col = (ep.path or "").ljust(path_w) if path_w else ""
                    file_col = f"{ep.file}:{ep.line}"
                    if path_col.strip():
                        lines.append(f"      {name_col}  {path_col}  {file_col}")
                    else:
                        lines.append(f"      {name_col}  {file_col}")

            lines.append("")

    return "\n".join(lines).rstrip()


def _filter_by_type(endpoints: list[Endpoint], type_filter: str) -> list[Endpoint]:
    """Filter endpoints by the --type flag value."""
    if type_filter == "graphql":
        return [e for e in endpoints if e.kind in ("query", "mutation")]
    if type_filter == "rest":
        return [
            e
            for e in endpoints
            if e.kind in ("get", "post", "put", "patch", "delete", "request")
        ]
    if type_filter == "routes":
        return [
            e
            for e in endpoints
            if e.kind in ("loader", "action", "resource", "layout", "component")
        ]
    return endpoints
