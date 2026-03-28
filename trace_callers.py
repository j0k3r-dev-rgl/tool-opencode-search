#!/usr/bin/env python3
"""
trace_callers.py — Thin CLI entrypoint.

Delegates to trace_core for all analysis.
Accepts: <workspace> <file> <symbol> [options_json]

Options JSON (optional):
  recursive: bool   (default false)
  maxDepth:  int    (default 3)
  language:  string (default "auto") — "auto", "java", "ts"/"typescript"
"""

import json
import sys
from pathlib import Path

# Ensure the tools directory is on sys.path so trace_core is importable
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


def _init_languages(language: str):
    """Import only the analyzers needed based on language selection."""
    if language in ("auto", "java"):
        try:
            from trace_core.analyzers import java  # noqa: F401 — registers SUPPORTED
        except ImportError:
            if language == "java":
                print(
                    json.dumps(
                        {
                            "error": "Java tree-sitter parser not available. Install tree-sitter-java."
                        }
                    )
                )
                sys.exit(1)

    if language in ("auto", "ts", "typescript"):
        try:
            from trace_core.analyzers import typescript  # noqa: F401 — registers SUPPORTED
        except ImportError:
            if language in ("ts", "typescript"):
                print(
                    json.dumps(
                        {
                            "error": "TypeScript tree-sitter parser not available. Install tree-sitter-typescript."
                        }
                    )
                )
                sys.exit(1)


def main():
    if len(sys.argv) < 4:
        print(
            json.dumps(
                {
                    "error": "Usage: trace_callers.py <workspace> <file> <symbol> [options_json]"
                }
            )
        )
        sys.exit(1)

    workspace_str = sys.argv[1]
    file_str = sys.argv[2]
    symbol = sys.argv[3]

    options = {}
    if len(sys.argv) >= 5:
        try:
            options = json.loads(sys.argv[4])
        except json.JSONDecodeError:
            pass

    recursive = bool(options.get("recursive", False))
    max_depth = int(options.get("maxDepth", 3))
    language = str(options.get("language", "auto")).lower()

    # Initialize only the required language analyzers
    _init_languages(language)

    from trace_core.common import find_method, parse, relative_to_workspace
    from trace_core.traversal import (
        direct_trace_callers,
        recursive_reverse_trace,
    )

    workspace = Path(workspace_str).resolve()
    target_file = Path(file_str).resolve()

    if not target_file.exists():
        print(json.dumps({"error": f"File not found: {file_str}"}))
        sys.exit(1)

    tree, src = parse(target_file)
    if not tree:
        print(json.dumps({"error": f"Unsupported or unreadable file: {file_str}"}))
        sys.exit(1)

    if not find_method(target_file, tree, src, symbol):
        print(json.dumps({"error": f"Symbol '{symbol}' was not found in '{file_str}'"}))
        sys.exit(1)

    lang_filter = language if language != "auto" else None
    normalized = direct_trace_callers(workspace, target_file, symbol, lang_filter)

    result = {
        "target": {
            "file": str(target_file.relative_to(workspace)),
            "symbol": symbol,
        },
        "callers": normalized,
        "count": len(normalized),
        "mode": "direct-incoming-v1" if not recursive else "reverse-recursive-v3",
        "options": {
            "recursive": recursive,
            "maxDepth": max_depth,
            "language": language,
        },
        "directSummary": {
            "count": len(normalized),
            "byRelation": {
                rel: sum(1 for c in normalized if c["relation"] == rel)
                for rel in sorted({c["relation"] for c in normalized})
            },
        },
    }

    if recursive:
        safe_max_depth = max(1, max_depth)
        result["recursiveResult"] = recursive_reverse_trace(
            workspace, target_file, symbol, safe_max_depth, lang_filter
        )

    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(json.dumps({"error": str(exc), "trace": traceback.format_exc()}))
        sys.exit(1)
