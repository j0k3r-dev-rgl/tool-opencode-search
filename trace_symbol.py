#!/usr/bin/env python3
"""
trace_symbol.py — Thin CLI entrypoint for forward tracing.

Delegates to trace_core for all analysis.
Accepts: <workspace> <file> <symbol> [options_json]

Options JSON (optional):
  language: string (default "auto") — "auto", "java", "ts"/"typescript"
"""

import json
import sys
from pathlib import Path

_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


def _init_languages(language: str):
    """Import only the analyzers needed based on language selection."""
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


def main():
    if len(sys.argv) < 4:
        print(
            json.dumps(
                {
                    "error": "Usage: trace_symbol.py <workspace> <file> <symbol> [options_json]"
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

    language = str(options.get("language", "auto")).lower()

    _init_languages(language)

    from trace_core.common import find_method, parse
    from trace_core.forward import forward_trace

    workspace = Path(workspace_str).resolve()
    start_file = Path(file_str).resolve()

    if not start_file.exists():
        print(json.dumps({"error": f"File not found: {file_str}"}))
        sys.exit(1)

    tree, src = parse(start_file)
    if not tree:
        print(json.dumps({"error": f"Unsupported or unreadable file: {file_str}"}))
        sys.exit(1)

    if not find_method(start_file, tree, src, symbol):
        print(json.dumps({"error": f"Symbol '{symbol}' was not found in '{file_str}'"}))
        sys.exit(1)

    files = forward_trace(workspace, start_file, symbol, language)

    print(
        json.dumps(
            {
                "files": files,
                "count": len(files),
                "options": {"language": language},
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
