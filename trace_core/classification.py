"""
classification.py — V3 impact classification and entry-point detection.

Classifies callers into direct, indirect, probable public entry points,
and implementation/interface chains.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from trace_core.common import (
    cached_impl_index,
    cached_type_index,
    extract_declared_types,
    extract_implemented_interfaces,
    find_method,
    make_node_key,
    parse,
    relative_to_workspace,
    unique_by_key,
)

# ---------------------------------------------------------------------------
# Entry-point detection constants (imported lazily from analyzers)
# ---------------------------------------------------------------------------

_JAVA_CONTROLLER_ANNOTATIONS = {"@RestController", "@Controller"}
_JAVA_METHOD_ENTRY_ANNOTATIONS = {
    "@GetMapping",
    "@PostMapping",
    "@PutMapping",
    "@PatchMapping",
    "@DeleteMapping",
    "@QueryMapping",
    "@MutationMapping",
}


def _annotations_before_line(src: bytes, line_number: int) -> list[str]:
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


def detect_probable_entry_point(
    workspace: Path, file_path: Path, symbol: str
) -> dict | None:
    rel_file = relative_to_workspace(file_path, workspace)
    ext = file_path.suffix.lower()
    reasons: list[str] = []

    if ext in {".ts", ".tsx", ".js", ".jsx"}:
        normalized = rel_file.replace("\\", "/")
        if "/routes/api/" in f"/{normalized}":
            reasons.append("ts.route_api_file")
        elif "/routes/" in f"/{normalized}":
            reasons.append("ts.route_file")
        if symbol == "loader":
            reasons.append("ts.loader_symbol")
        elif symbol == "action":
            reasons.append("ts.action_symbol")
    elif ext == ".java":
        tree, src = parse(file_path)
        if not tree or not src:
            return None
        source_text = src.decode("utf-8", errors="replace")
        for annotation in _JAVA_CONTROLLER_ANNOTATIONS:
            if annotation in source_text:
                reasons.append(f"java.controller_annotation:{annotation}")
        method_node = find_method(file_path, tree, src, symbol)
        if method_node is not None:
            method_annotations = _annotations_before_line(
                src, method_node.start_point[0] + 1
            )
            for annotation in method_annotations:
                if annotation in _JAVA_METHOD_ENTRY_ANNOTATIONS:
                    reasons.append(f"java.method_annotation:{annotation}")

    if not reasons:
        return None

    return {"file": rel_file, "symbol": symbol, "reasons": sorted(set(reasons))}


# ---------------------------------------------------------------------------
# Reverse path helpers
# ---------------------------------------------------------------------------


def collect_reverse_paths(
    adjacency: dict[str, list[str]],
    root_key: str,
    node_meta: dict[str, dict],
    cycles: list[dict],
) -> list[list[dict]]:
    cycle_edges = {(c["from"], c["to"]) for c in cycles}
    paths: list[list[dict]] = []

    def dfs(current: str, trail: list[str], seen: set[str]):
        next_nodes = adjacency.get(current, [])
        expanded = False
        for nxt in next_nodes:
            if (current, nxt) in cycle_edges or nxt in seen:
                continue
            expanded = True
            dfs(nxt, trail + [nxt], seen | {nxt})
        if not expanded:
            paths.append([node_meta[k] for k in trail])

    dfs(root_key, [root_key], {root_key})
    return paths


def shortest_paths_from_root(
    adjacency: dict[str, list[str]], root_key: str
) -> dict[str, list[str]]:
    paths: dict[str, list[str]] = {root_key: [root_key]}
    queue: deque[str] = deque([root_key])
    while queue:
        current = queue.popleft()
        for nxt in adjacency.get(current, []):
            if nxt in paths:
                continue
            paths[nxt] = paths[current] + [nxt]
            queue.append(nxt)
    return paths


def _node_path_summary(path_keys: list[str], node_meta: dict[str, dict]) -> list[dict]:
    return [
        {"file": node_meta[k]["file"], "symbol": node_meta[k]["symbol"]}
        for k in path_keys
    ]


def _is_java_interface_file(path: Path) -> bool:
    from trace_core.common import walk

    tree, src = parse(path)
    if not tree or not src:
        return False
    return bool(walk(tree.root_node, "interface_declaration"))


# ---------------------------------------------------------------------------
# V3 impact classification
# ---------------------------------------------------------------------------


def classify_recursive_impact(
    workspace: Path,
    target_file: Path,
    symbol: str,
    root_key: str,
    node_meta: dict[str, dict],
    adjacency: dict[str, list[str]],
    direct_by_node: dict[str, list[dict]],
    probable_entry_points: dict[str, dict],
) -> dict:
    shortest = shortest_paths_from_root(adjacency, root_key)
    type_index = cached_type_index(workspace)
    impl_index = cached_impl_index(workspace)
    target_tree, target_src = parse(target_file)

    root_declared_types = (
        extract_declared_types(target_file, target_tree, target_src)
        if target_tree and target_src
        else set()
    )
    root_implemented_interfaces = (
        extract_implemented_interfaces(target_tree, target_src)
        if target_tree and target_src and target_file.suffix.lower() == ".java"
        else set()
    )

    all_matches: list[dict] = []
    for target_node_key, callers in direct_by_node.items():
        target_node = node_meta.get(target_node_key)
        if not target_node:
            continue
        for caller in callers:
            traverse_symbol = caller.get("traverse_symbol", caller["caller"])
            caller_key = make_node_key(caller["file"], traverse_symbol)
            caller_node = node_meta.get(caller_key)
            depth = (
                caller_node.get("depth", target_node.get("depth", 0) + 1)
                if caller_node
                else target_node.get("depth", 0) + 1
            )
            path_keys = shortest.get(caller_key, [])
            all_matches.append(
                {
                    "file": caller["file"],
                    "symbol": traverse_symbol,
                    "caller": caller["caller"],
                    "depth": depth,
                    "line": caller["line"],
                    "column": caller["column"],
                    "relation": caller["relation"],
                    "receiverType": caller.get("receiverType"),
                    "snippet": caller.get("snippet", ""),
                    "calls": {
                        "file": target_node["file"],
                        "symbol": target_node["symbol"],
                    },
                    "pathFromTarget": _node_path_summary(path_keys, node_meta)
                    if path_keys
                    else [],
                }
            )

    flattened = unique_by_key(
        all_matches,
        lambda m: (
            m["file"],
            m["symbol"],
            m["line"],
            m["column"],
            m["relation"],
            m["calls"]["file"],
            m["calls"]["symbol"],
        ),
    )
    flattened.sort(
        key=lambda m: (m["depth"], m["file"], m["symbol"], m["line"], m["column"])
    )

    direct_callers = [m for m in flattened if m["depth"] == 1]
    indirect_callers = [m for m in flattened if m["depth"] > 1]

    probable_public = []
    for key in sorted(
        probable_entry_points,
        key=lambda k: (
            node_meta.get(k, {}).get("depth", 10**9),
            probable_entry_points[k]["file"],
            probable_entry_points[k]["symbol"],
        ),
    ):
        path_keys = shortest.get(key, [])
        probable_public.append(
            probable_entry_points[key]
            | {
                "probable": True,
                "depth": node_meta.get(key, {}).get("depth"),
                "pathFromTarget": _node_path_summary(path_keys, node_meta)
                if path_keys
                else [],
            }
        )

    # Interface chains
    chain_groups: dict[tuple[str, str, str], dict] = {}
    for item in flattened:
        if item["relation"] != "java.interface_dispatch" or not item.get(
            "receiverType"
        ):
            continue
        gk = (item["receiverType"], item["calls"]["file"], item["calls"]["symbol"])
        interface_file = type_index.get(item["receiverType"])
        chain_groups.setdefault(
            gk,
            {
                "kind": "java.interface_dispatch_chain",
                "probable": True,
                "interface": {
                    "name": item["receiverType"],
                    "file": relative_to_workspace(interface_file, workspace)
                    if interface_file and interface_file.exists()
                    else None,
                },
                "implementation": {
                    "file": item["calls"]["file"],
                    "symbol": item["calls"]["symbol"],
                },
                "callers": [],
            },
        )
        chain_groups[gk]["callers"].append(
            {
                k: item[k]
                for k in (
                    "file",
                    "symbol",
                    "depth",
                    "line",
                    "column",
                    "relation",
                    "snippet",
                    "pathFromTarget",
                )
            }
        )

    if target_file.suffix.lower() == ".java":
        root_rel = relative_to_workspace(target_file, workspace)
        for iface_name in sorted(root_implemented_interfaces):
            iface_file = type_index.get(iface_name)
            gk = (iface_name, root_rel, symbol)
            existing = chain_groups.get(gk)
            chain_groups[gk] = {
                "kind": "java.target_implements_interface",
                "probable": True,
                "interface": {
                    "name": iface_name,
                    "file": relative_to_workspace(iface_file, workspace)
                    if iface_file and iface_file.exists()
                    else None,
                },
                "implementation": {"file": root_rel, "symbol": symbol},
                "callers": existing["callers"] if existing else [],
            }

        if _is_java_interface_file(target_file):
            for dtype in sorted(root_declared_types):
                implementations = [
                    {"file": relative_to_workspace(p, workspace)}
                    for p in sorted(impl_index.get(dtype, []), key=str)
                ]
                if not implementations:
                    continue
                chain_groups[(dtype, root_rel, symbol)] = {
                    "kind": "java.interface_has_implementations",
                    "probable": True,
                    "interface": {"name": dtype, "file": root_rel, "symbol": symbol},
                    "implementations": implementations,
                    "callers": chain_groups.get((dtype, root_rel, symbol), {}).get(
                        "callers", []
                    ),
                }

    impl_chains = sorted(
        chain_groups.values(),
        key=lambda c: (
            c.get("implementation", {}).get("file", ""),
            c.get("interface", {}).get("name", ""),
            c.get("kind", ""),
        ),
    )

    return {
        "summary": {
            "directCallerCount": len(direct_callers),
            "indirectCallerCount": len(indirect_callers),
            "probablePublicEntryPointCount": len(probable_public),
            "implementationInterfaceChainCount": len(impl_chains),
        },
        "directCallers": direct_callers,
        "indirectCallers": indirect_callers,
        "probablePublicEntryPoints": probable_public,
        "implementationInterfaceChain": impl_chains,
    }
