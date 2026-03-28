"""
forward.py — Forward trace (trace_symbol): follows calls from a symbol outward.

BFS traversal that resolves types and follows internal references
from the entry point down to leaf functions/adapters.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from trace_core.common import (
    cached_impl_index,
    cached_type_index,
    find_method,
    is_in_workspace,
    parse,
    supported_files,
)

# ---------------------------------------------------------------------------
# Lazy analyzer imports
# ---------------------------------------------------------------------------

_java_analyzer = None
_ts_analyzer = None


def _get_java():
    global _java_analyzer
    if _java_analyzer is None:
        from trace_core.analyzers import java as _m

        _java_analyzer = _m
    return _java_analyzer


def _get_ts():
    global _ts_analyzer
    if _ts_analyzer is None:
        from trace_core.analyzers import typescript as _m

        _ts_analyzer = _m
    return _ts_analyzer


# ---------------------------------------------------------------------------
# Forward BFS trace
# ---------------------------------------------------------------------------


def forward_trace(
    workspace: Path,
    start_file: Path,
    symbol: str,
    language: str | None = None,
) -> list[str]:
    """
    BFS forward trace from start_file::symbol.
    Returns list of relative file paths involved in the call chain.
    """
    type_index = cached_type_index(workspace)
    impl_index = cached_impl_index(workspace)

    visited_files: list[Path] = []
    visited_set: set[Path] = set()
    visited_pairs: set[tuple[Path, str]] = set()

    queue: deque[tuple[Path, str]] = deque()
    queue.append((start_file, symbol))

    java = _get_java()
    ts = _get_ts()

    while queue:
        current_file, current_symbol = queue.popleft()

        pair = (current_file, current_symbol)
        if pair in visited_pairs:
            continue
        visited_pairs.add(pair)

        if current_file not in visited_set:
            visited_set.add(current_file)
            visited_files.append(current_file)

        tree, src = parse(current_file)
        if not tree:
            continue

        ext = current_file.suffix.lower()
        method_node = find_method(current_file, tree, src, current_symbol)
        if not method_node:
            continue

        if ext == ".java":
            refs = java.extract_references(method_node, tree, src)

            for receiver_type, method_called in refs:
                if receiver_type == "__new__":
                    if method_called in java.JAVA_SKIP_TYPES:
                        continue
                    target = type_index.get(method_called)
                    if target and is_in_workspace(target, workspace):
                        queue.append((target, method_called))
                    continue

                if receiver_type == "__self__":
                    queue.append((current_file, method_called))
                    continue

                if receiver_type in java.JAVA_SKIP_TYPES:
                    continue

                target_file = type_index.get(receiver_type)
                if not target_file or not is_in_workspace(target_file, workspace):
                    continue

                queue.append((target_file, method_called))

                impl_files = impl_index.get(receiver_type, [])
                for impl_file in impl_files:
                    if is_in_workspace(impl_file, workspace):
                        queue.append((impl_file, method_called))

        else:
            resolved_refs = ts.extract_references(
                method_node, tree, src, current_file, workspace
            )

            for ref_file, ref_sym in resolved_refs:
                if ref_file not in visited_set:
                    visited_set.add(ref_file)
                    visited_files.append(ref_file)
                queue.append((ref_file, ref_sym))

    return [str(f.relative_to(workspace)) for f in visited_files]
