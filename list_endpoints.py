#!/usr/bin/env python3
"""
list_endpoints.py — Thin CLI entrypoint for endpoint/route discovery.

Delegates to endpoints_core for all analysis.
Accepts: <workspace> [options_json]

Options JSON (optional):
  language: string (default "auto") — "auto", "java", "typescript"
  type:     string (default "any")  — "any", "graphql", "rest", "routes"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


def _init_analyzers(language: str) -> None:
    """Import only the analyzers needed based on language selection."""
    if language in ("auto", "java"):
        try:
            import tree_sitter_java  # noqa: F401
        except ImportError:
            if language == "java":
                print(
                    json.dumps(
                        {
                            "error": "tree-sitter-java not available. Install with: uv pip install tree-sitter-java"
                        }
                    )
                )
                sys.exit(1)

    if language in ("auto", "typescript"):
        # TypeScript analyzer uses regex only — no tree-sitter needed
        pass

    # Import analyzers — triggers registration into endpoints_core.common.ANALYZERS
    if language == "java":
        from endpoints_core.analyzers import java  # noqa: F401
    elif language == "typescript":
        from endpoints_core.analyzers import typescript  # noqa: F401
    else:
        from endpoints_core.analyzers import java, typescript  # noqa: F401


def main() -> None:
    if len(sys.argv) < 2:
        print(
            json.dumps({"error": "Usage: list_endpoints.py <workspace> [options_json]"})
        )
        sys.exit(1)

    workspace_str = sys.argv[1]
    options: dict = {}
    if len(sys.argv) >= 3:
        try:
            options = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            pass

    language = str(options.get("language", "auto")).lower()
    type_filter = str(options.get("type", "any")).lower()

    _init_analyzers(language)

    from endpoints_core.common import ANALYZERS
    from endpoints_core.formatter import format_results

    workspace = Path(workspace_str).resolve()
    if not workspace.exists():
        print(json.dumps({"error": f"Workspace not found: {workspace_str}"}))
        sys.exit(1)

    results = []
    for lang_id, analyze_fn in ANALYZERS:
        if language != "auto" and lang_id != language:
            continue
        try:
            result = analyze_fn(workspace, options)
            results.append(result)
        except Exception as exc:
            import traceback

            results.append(
                {
                    "language": lang_id,
                    "error": str(exc),
                    "trace": traceback.format_exc(),
                }
            )

    # Separate clean results from errors
    clean = [r for r in results if hasattr(r, "endpoints")]
    errors = [r for r in results if not hasattr(r, "endpoints")]

    total = sum(len(r.endpoints) for r in clean)
    formatted = format_results(clean, type_filter)

    output: dict = {
        "formatted": formatted,
        "total": total,
        "byLanguage": [
            {
                "language": r.language,
                "framework": r.framework,
                "count": len(r.endpoints),
                "endpoints": [
                    {
                        "name": e.name,
                        "kind": e.kind,
                        "file": e.file,
                        "line": e.line,
                        "path": e.path,
                        "group": e.group,
                    }
                    for e in r.endpoints
                ],
            }
            for r in clean
        ],
    }

    if errors:
        output["errors"] = errors

    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        print(json.dumps({"error": str(exc), "trace": traceback.format_exc()}))
        sys.exit(1)
