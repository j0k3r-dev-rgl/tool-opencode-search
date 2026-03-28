"""
traversal.py — Direct trace and recursive reverse traversal orchestration.

Dispatches to the correct language analyzer based on file extension.
Builds the reverse graph, detects cycles, and controls depth.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from trace_core.common import (
    cached_impl_index,
    cached_type_index,
    extract_implemented_interfaces,
    find_method,
    make_node_key,
    parse,
    relative_to_workspace,
    supported_files,
    unique_by_key,
    unique_callers,
)
from trace_core.classification import (
    classify_recursive_impact,
    collect_reverse_paths,
    detect_probable_entry_point,
)

# ---------------------------------------------------------------------------
# Lazy analyzer imports — only loaded when needed
# ---------------------------------------------------------------------------

_java_analyzer = None
_ts_analyzer = None


def _get_java_analyzer():
    global _java_analyzer
    if _java_analyzer is None:
        from trace_core.analyzers import java as _java_mod

        _java_analyzer = _java_mod
    return _java_analyzer


def _get_ts_analyzer():
    global _ts_analyzer
    if _ts_analyzer is None:
        from trace_core.analyzers import typescript as _ts_mod

        _ts_analyzer = _ts_mod
    return _ts_analyzer


# ---------------------------------------------------------------------------
# Direct trace
# ---------------------------------------------------------------------------


def direct_trace_callers(
    workspace: Path,
    target_file: Path,
    symbol: str,
    language: str | None = None,
) -> list[dict]:
    """Find all direct callers/references of target_file::symbol."""
    tree, src = parse(target_file)
    if not tree or not src:
        return []

    type_index = cached_type_index(workspace)
    impl_index = cached_impl_index(workspace)

    ext = target_file.suffix.lower()
    is_ts = ext in {".ts", ".tsx", ".js", ".jsx"}

    target_default_exported = False
    if is_ts:
        ts_analyzer = _get_ts_analyzer()
        target_default_exported = ts_analyzer.is_ts_symbol_default_exported(
            tree, src, symbol
        )

    target_interfaces = set()
    if ext == ".java":
        target_interfaces = extract_implemented_interfaces(tree, src)

    callers: list[dict] = []
    for path in supported_files(workspace, language):
        if path == target_file:
            continue

        if path.suffix.lower() == ".java":
            if language and language not in ("java", "auto", None):
                continue
            java_analyzer = _get_java_analyzer()
            callers.extend(
                java_analyzer.analyze_file(
                    path,
                    target_file,
                    symbol,
                    type_index,
                    target_interfaces,
                    impl_index,
                )
            )
        else:
            if language and language not in ("ts", "typescript", "auto", None):
                continue
            ts_analyzer = _get_ts_analyzer()
            callers.extend(
                ts_analyzer.analyze_file(
                    path, workspace, target_file, symbol, target_default_exported
                )
            )

    callers.sort(key=lambda c: (c["file"], c["line"], c["column"]))
    return [
        {**c, "file": relative_to_workspace(Path(c["file"]), workspace)}
        for c in unique_callers(callers)
    ]


# ---------------------------------------------------------------------------
# Recursive reverse trace
# ---------------------------------------------------------------------------


def recursive_reverse_trace(
    workspace: Path,
    target_file: Path,
    symbol: str,
    max_depth: int,
    language: str | None = None,
) -> dict:
    root_rel_file = relative_to_workspace(target_file, workspace)
    root_key = make_node_key(root_rel_file, symbol)
    node_meta: dict[str, dict] = {
        root_key: {"file": root_rel_file, "symbol": symbol, "depth": 0}
    }
    adjacency: dict[str, list[str]] = defaultdict(list)
    direct_by_node: dict[str, list[dict]] = {}
    cycles: list[dict] = []
    truncated: list[dict] = []
    visited: set[str] = set()
    probable_entry_points: dict[str, dict] = {}
    max_depth_observed = 0

    def visit(
        current_file: Path,
        current_symbol: str,
        depth: int,
        ancestry: list[str],
    ):
        nonlocal max_depth_observed
        current_rel = relative_to_workspace(current_file, workspace)
        current_key = make_node_key(current_rel, current_symbol)
        max_depth_observed = max(max_depth_observed, depth)

        if current_key in visited:
            return
        visited.add(current_key)

        callers = direct_trace_callers(
            workspace, current_file, current_symbol, language
        )
        direct_by_node[current_key] = callers

        for caller in callers:
            traverse_symbol = caller.get("traverse_symbol", caller["caller"])
            child_key = make_node_key(caller["file"], traverse_symbol)
            child_meta = {
                "file": caller["file"],
                "symbol": traverse_symbol,
                "depth": depth + 1,
                "via": {
                    "relation": caller["relation"],
                    "line": caller["line"],
                    "column": caller["column"],
                    "snippet": caller.get("snippet", ""),
                },
            }
            if (
                child_key not in node_meta
                or node_meta[child_key].get("depth", 10**9) > depth + 1
            ):
                node_meta[child_key] = child_meta

            if child_key not in adjacency[current_key]:
                adjacency[current_key].append(child_key)

            entry = detect_probable_entry_point(
                workspace, workspace / caller["file"], traverse_symbol
            )
            if entry:
                probable_entry_points[child_key] = entry

            if child_key in ancestry:
                cycles.append(
                    {
                        "from": current_key,
                        "to": child_key,
                        "path": ancestry + [child_key],
                    }
                )
                continue

            if traverse_symbol == "<module>":
                continue

            if depth >= max_depth:
                truncated.append(
                    {
                        "node": child_key,
                        "file": caller["file"],
                        "symbol": traverse_symbol,
                        "depth": depth + 1,
                    }
                )
                continue

            visit(
                workspace / caller["file"],
                traverse_symbol,
                depth + 1,
                ancestry + [child_key],
            )

    visit(target_file, symbol, 0, [root_key])

    for key in adjacency:
        adjacency[key] = sorted(
            adjacency[key],
            key=lambda k: (node_meta[k]["file"], node_meta[k]["symbol"]),
        )

    paths = collect_reverse_paths(adjacency, root_key, node_meta, cycles)
    summarized_paths = [p for p in paths if len(p) > 1]

    classifications = classify_recursive_impact(
        workspace,
        target_file,
        symbol,
        root_key,
        node_meta,
        dict(adjacency),
        direct_by_node,
        probable_entry_points,
    )

    return {
        "enabled": True,
        "root": node_meta[root_key],
        "maxDepth": max_depth,
        "maxDepthObserved": max_depth_observed,
        "nodeCount": len(node_meta),
        "edgeCount": sum(len(children) for children in adjacency.values()),
        "nodes": [node_meta[k] | {"key": k} for k in sorted(node_meta)],
        "adjacency": dict(adjacency),
        "paths": summarized_paths,
        "pathCount": len(summarized_paths),
        "cycles": cycles,
        "truncated": unique_by_key(
            truncated,
            lambda t: (t["node"], t["file"], t["symbol"], t["depth"]),
        ),
        "probableEntryPoints": [
            probable_entry_points[k] | {"key": k}
            for k in sorted(
                probable_entry_points,
                key=lambda k: (
                    probable_entry_points[k]["file"],
                    probable_entry_points[k]["symbol"],
                ),
            )
        ],
        "directByNode": direct_by_node,
        "classifications": classifications,
    }
