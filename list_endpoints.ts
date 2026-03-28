/**
 * list_endpoints — Scan the workspace and return all API endpoints and routes.
 *
 * Detects:
 *   Java/Spring:        @QueryMapping, @MutationMapping, @GetMapping, @PostMapping,
 *                       @PutMapping, @PatchMapping, @DeleteMapping
 *   TypeScript/RR7:     loader, action exports in routes/ files; resource routes under routes/api/
 *
 * Returns a compact index — name, kind, file, line, path — no file content.
 * Modular: new languages can be added by registering an analyzer in endpoints_core/analyzers/.
 *
 * Examples:
 *   list_endpoints()                          — all languages, all types
 *   list_endpoints(language="java")           — Spring endpoints only
 *   list_endpoints(language="java", type="graphql")   — only @QueryMapping / @MutationMapping
 *   list_endpoints(language="java", type="rest")      — only HTTP mappings
 *   list_endpoints(language="typescript")     — React Router 7 routes only
 *   list_endpoints(language="typescript", type="routes") — loaders and actions only
 */

import { tool } from "@opencode-ai/plugin"
import * as path from "node:path"
import * as fs from "node:fs/promises"

const SCRIPT = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "list_endpoints.py",
)

function uvDepsForLanguage(language: string): string[] {
  const base: string[] = []
  switch (language) {
    case "java":
      return [...base, "--with", "tree-sitter", "--with", "tree-sitter-java"]
    case "typescript":
      return base  // TS analyzer uses regex only — no tree-sitter needed
    case "auto":
    default:
      return [...base, "--with", "tree-sitter", "--with", "tree-sitter-java"]
  }
}

export default tool({
  description: `Scan the workspace and return all API endpoints and frontend routes — no file content.

Returns a compact index: name, kind, file, line, and HTTP path when available.
Use this to understand the full API surface of a project before reading individual files.

Supports:
  - Java / Spring Boot: @QueryMapping, @MutationMapping, @GetMapping, @PostMapping,
                        @PutMapping, @PatchMapping, @DeleteMapping
  - TypeScript / React Router 7: loader, action exports; resource routes under routes/api/

Modular — new languages can be added without changing this tool.

Examples:
  list_endpoints()
  list_endpoints(language="java", type="graphql")
  list_endpoints(language="java", type="rest")
  list_endpoints(language="typescript")
  list_endpoints(language="typescript", type="routes")`,

  args: {
    language: tool.schema
      .enum(["auto", "java", "typescript"])
      .optional()
      .describe("Language to scan. 'auto' runs all registered analyzers. Defaults to 'auto'."),

    type: tool.schema
      .enum(["any", "graphql", "rest", "routes"])
      .optional()
      .describe("Filter by endpoint type. 'graphql' = @QueryMapping/@MutationMapping. 'rest' = HTTP mappings. 'routes' = RR7 loaders/actions/layouts. Defaults to 'any'."),

    includeComponents: tool.schema
      .boolean()
      .optional()
      .describe("Include component-only pages (routes with no loader or action). Default: false. Enable when you need the full route inventory."),
  },

  async execute(args, context) {
    const workspace = context.directory ?? process.cwd()
    const resolvedWorkspace = path.resolve(workspace)

    try {
      await fs.access(SCRIPT)
    } catch {
      return `list_endpoints.py not found at: ${SCRIPT}`
    }

    const language = args.language ?? "auto"
    const type = args.type ?? "any"
    const includeComponents = args.includeComponents ?? false
    const uvDeps = uvDepsForLanguage(language)

    let stdout = ""
    let stderr = ""

    try {
      const uvArgs = uvDeps.length > 0
        ?           ["uv", "run", ...uvDeps, "python3", SCRIPT, resolvedWorkspace, JSON.stringify({ language, type, includeComponents })]
        : ["uv", "run", "python3", SCRIPT, resolvedWorkspace, JSON.stringify({ language, type, includeComponents })]

      const proc = Bun.spawn(uvArgs, {
        stdout: "pipe",
        stderr: "pipe",
        cwd: workspace,
      })

      stdout = await new Response(proc.stdout).text()
      stderr = await new Response(proc.stderr).text()
      const exitCode = await proc.exited

      if (exitCode !== 0) {
        return `list_endpoints failed (exit ${exitCode})\n\n${stderr || stdout}`
      }
    } catch (e) {
      return `Failed to run uv: ${e instanceof Error ? e.message : String(e)}\n\nMake sure 'uv' is installed and in PATH.`
    }

    let result: {
      formatted?: string
      total?: number
      byLanguage?: Array<{ language: string; framework: string; count: number }>
      errors?: unknown[]
      error?: string
      trace?: string
    }

    try {
      result = JSON.parse(stdout.trim())
    } catch {
      return `Could not parse output:\n${stdout}\n\nStderr:\n${stderr}`
    }

    if (result.error) {
      return `Error:\n${result.error}${result.trace ? `\n\nTraceback:\n${result.trace}` : ""}`
    }

    if (!result.formatted || result.total === 0) {
      return `No endpoints found in '${workspace}'.`
    }

    const filterParts: string[] = []
    if (language !== "auto") filterParts.push(`language: ${language}`)
    if (type !== "any") filterParts.push(`type: ${type}`)
    const filterStr = filterParts.length > 0 ? `\nFilters: ${filterParts.join(", ")}` : ""

    const errorNote = result.errors && result.errors.length > 0
      ? `\n\nWarnings: ${result.errors.length} analyzer(s) reported errors.`
      : ""

    return [
      `Workspace: ${path.relative(process.cwd(), resolvedWorkspace) || "."}${filterStr}`,
      "",
      result.formatted,
      errorNote,
    ].join("\n").trim()
  },
})
