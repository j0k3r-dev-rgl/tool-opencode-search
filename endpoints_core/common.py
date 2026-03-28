"""
common.py — Shared types, file walker, and registry for endpoint analyzers.

Each analyzer module registers itself by appending to ANALYZERS on import.
The CLI (list_endpoints.py) iterates ANALYZERS and collects results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


@dataclass
class Endpoint:
    """A single discovered endpoint / route."""

    name: str  # method name, loader, action, route path
    kind: str  # query | mutation | get | post | put | patch | delete | loader | action | resource
    file: str  # path relative to workspace
    line: int  # 1-indexed line of the declaration
    path: str | None  # HTTP path if available (e.g. "/titulares"), None if not
    group: str  # controller class name or route filename for grouping


@dataclass
class AnalyzerResult:
    """Result returned by each analyzer."""

    language: str  # "java" | "typescript" | "python" | etc.
    framework: str  # "spring" | "react-router-7" | "fastapi" | etc.
    endpoints: list[Endpoint] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analyzer registry
# ---------------------------------------------------------------------------

# Each entry: (language_id, analyze_fn)
# language_id must match the values accepted by the CLI --language flag.
# analyze_fn signature: (workspace: Path, options: dict) -> AnalyzerResult
AnalyzeFn = Callable[[Path, dict], AnalyzerResult]

ANALYZERS: list[tuple[str, AnalyzeFn]] = []


def register(language_id: str, fn: AnalyzeFn) -> None:
    """Register an analyzer for a given language id."""
    ANALYZERS.append((language_id, fn))


# ---------------------------------------------------------------------------
# Shared file-system helpers
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    "node_modules",
    ".git",
    "target",
    "build",
    "dist",
    ".next",
    "__pycache__",
    ".gradle",
    ".idea",
    "out",
    ".cache",
    ".ruff_cache",
    ".react-router",
}


def walk_files(workspace: Path, extensions: set[str]) -> list[Path]:
    """Yield workspace files matching the given extensions, skipping noise dirs."""
    results: list[Path] = []
    for p in workspace.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in extensions:
            continue
        if any(skip in p.parts for skip in SKIP_DIRS):
            continue
        results.append(p)
    return sorted(results)


def relative(path: Path, workspace: Path) -> str:
    """Return path relative to workspace as a forward-slash string."""
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path)
