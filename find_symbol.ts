/**
 * find_symbol — Locate where a symbol is defined in the workspace.
 *
 * Given a name (class, interface, function, method), scans the workspace AST
 * and returns all files + lines where that symbol is defined.
 *
 * Language-aware: only loads the tree-sitter deps needed for the selected language.
 * Supports exact match (default) and fuzzy match (contains).
 *
 * Supports: Java (Spring hexagonal), TypeScript/TSX/JavaScript/JSX (React Router 7).
 */

import { tool } from "@opencode-ai/plugin"
import * as path from "node:path"
import * as fs from "node:fs/promises"

const SCRIPT = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "find_symbol.py",
)

function uvDepsForLanguage(language: string): string[] {
  const base = ["--with", "tree-sitter"]
  switch (language) {
    case "java":
      return [...base, "--with", "tree-sitter-java"]
    case "ts":
    case "typescript":
      return [...base, "--with", "tree-sitter-typescript"]
    case "auto":
    default:
      return [...base, "--with", "tree-sitter-java", "--with", "tree-sitter-typescript"]
  }
}

export default tool({
  description: `Locate where a symbol is defined in the workspace using AST analysis (tree-sitter).

Given a name, scans the workspace and returns all files + lines where that class, interface,
function, method, or type is defined. Returns only file + line + kind — no file content.

Use this when you know a symbol name but not its file. Much cheaper than grep or manual search.

Supports exact match (default) and fuzzy/contains match.
Supports language filter to avoid loading unnecessary parsers.

Supports: Java (Spring hexagonal), TypeScript/TSX/JavaScript/JSX (React Router 7).

Examples:
  find_symbol(name="CreateTitularUseCase", language="java")
  find_symbol(name="getTitularById", language="java", kind="method")
  find_symbol(name="useSession", language="ts")
  find_symbol(name="Titular", language="java", fuzzy=true)
  find_symbol(name="loader", language="ts", kind="function")`,

  args: {
    name: tool.schema
      .string()
      .describe("Symbol name to search for. Exact match by default. Use fuzzy=true for partial/contains match."),

    language: tool.schema
      .enum(["auto", "java", "ts", "typescript"])
      .optional()
      .describe("Language to search in. 'auto' searches all supported files. Prefer specifying a language to speed up the scan. Defaults to 'auto'."),

    kind: tool.schema
      .enum(["any", "class", "interface", "function", "method", "type", "enum", "constructor", "annotation"])
      .optional()
      .describe("Filter results by definition kind. Defaults to 'any' (all kinds)."),

    fuzzy: tool.schema
      .boolean()
      .optional()
      .describe("If true, match names that CONTAIN the search term (case-insensitive). Default: false (exact match)."),
  },

  async execute(args, context) {
    const workspace = context.directory ?? process.cwd()
    const resolvedWorkspace = path.resolve(workspace)

    try {
      await fs.access(SCRIPT)
    } catch {
      return `find_symbol.py not found at: ${SCRIPT}`
    }

    const language = args.language ?? "auto"
    const kind = args.kind ?? "any"
    const fuzzy = args.fuzzy ?? false
    const uvDeps = uvDepsForLanguage(language)

    let stdout = ""
    let stderr = ""

    try {
      const proc = Bun.spawn(
        [
          "uv", "run",
          ...uvDeps,
          "python3", SCRIPT,
          resolvedWorkspace,
          args.name,
          JSON.stringify({ language, kind, fuzzy }),
        ],
        {
          stdout: "pipe",
          stderr: "pipe",
          cwd: workspace,
        },
      )

      stdout = await new Response(proc.stdout).text()
      stderr = await new Response(proc.stderr).text()
      const exitCode = await proc.exited

      if (exitCode !== 0) {
        return `find_symbol failed (exit ${exitCode})\n\n${stderr || stdout}`
      }
    } catch (e) {
      return `Failed to run uv: ${e instanceof Error ? e.message : String(e)}\n\nMake sure 'uv' is installed and in PATH.`
    }

    let result: {
      matches?: Array<{ file: string; line: number; kind: string; name: string }>
      count?: number
      error?: string
      trace?: string
      options?: { language?: string; kind?: string; fuzzy?: boolean }
    }

    try {
      result = JSON.parse(stdout.trim())
    } catch {
      return `Could not parse output:\n${stdout}\n\nStderr:\n${stderr}`
    }

    if (result.error) {
      return `Error:\n${result.error}${result.trace ? `\n\nTraceback:\n${result.trace}` : ""}`
    }

    if (!result.matches || result.matches.length === 0) {
      const hint = fuzzy
        ? `No definitions found containing '${args.name}'`
        : `No definitions found for '${args.name}'. Try fuzzy=true for partial matches.`
      return hint
    }

    const lines = result.matches.map(
      (m, i) => `  ${i + 1}. ${m.file}:${m.line}  [${m.kind}] ${m.name}`,
    )

    const filterParts: string[] = []
    if (language !== "auto") filterParts.push(`language: ${language}`)
    if (kind !== "any") filterParts.push(`kind: ${kind}`)
    if (fuzzy) filterParts.push("fuzzy: true")
    const filterStr = filterParts.length > 0 ? `\nFilters: ${filterParts.join(", ")}` : ""

    return [
      `Symbol: \`${args.name}\``,
      `Matches: ${result.count}${filterStr}`,
      "",
      lines.join("\n"),
      "",
      "Use read or scan_module to inspect the file content.",
    ].join("\n")
  },
})
