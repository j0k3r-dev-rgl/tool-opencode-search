/**
 * trace_symbol — Forward trace: follow all internal references from a symbol.
 *
 * Given a file and a method name, recursively follows all internal references
 * (calls, interface implementations, injected dependencies) and returns the
 * complete list of project files involved — from the entry point down to the
 * final adapter or leaf function.
 *
 * Language-aware: only loads the tree-sitter deps needed for the selected language.
 *
 * Supports: Java (Spring hexagonal), TypeScript/TSX/JavaScript/JSX (React Router 7).
 */

import { tool } from "@opencode-ai/plugin"
import * as path from "node:path"
import * as fs from "node:fs/promises"

const SCRIPT = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "trace_symbol.py",
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
  description: `Trace all files related to a method or function using AST analysis (tree-sitter).

Given a file and a method name, recursively follows all internal references
(calls, interface implementations, injected dependencies) and returns the
complete list of project files involved — from the entry point down to the
final adapter or leaf function.

Skips: standard library calls, framework methods, external packages.
Supports language selection to optimize execution.

Supports: Java (Spring hexagonal), TypeScript/TSX/JavaScript/JSX (React Router 7).

Use this before reading a feature — get the file list first, then read only what you need.

Examples:
  trace_symbol(file="back/.../RootUserGraphQLController.java", symbol="getUserByIdRoot", language="java")
  trace_symbol(file="front/app/routes/titular/titular.tsx", symbol="loader", language="ts")`,

  args: {
    file: tool.schema
      .string()
      .describe("Path to the file containing the method. Can be absolute or relative to workspace."),

    symbol: tool.schema
      .string()
      .describe("Exact name of the method or function to trace."),

    language: tool.schema
      .enum(["auto", "java", "ts", "typescript"])
      .optional()
      .describe("Language to analyze. 'auto' loads all parsers. 'java' or 'ts'/'typescript' loads only what is needed. Defaults to 'auto'."),
  },

  async execute(args, context) {
    const workspace = context.directory ?? process.cwd()

    const filePath = path.isAbsolute(args.file)
      ? args.file
      : path.join(workspace, args.file)

    const resolvedFile = path.resolve(filePath)
    const resolvedWorkspace = path.resolve(workspace)
    if (!resolvedFile.startsWith(resolvedWorkspace + path.sep) && resolvedFile !== resolvedWorkspace) {
      return `Access denied: '${args.file}' is outside the current workspace`
    }

    try {
      await fs.access(resolvedFile)
    } catch {
      return `File not found: '${args.file}'`
    }

    try {
      await fs.access(SCRIPT)
    } catch {
      return `trace_symbol.py not found at: ${SCRIPT}`
    }

    const language = args.language ?? "auto"
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
          resolvedFile,
          args.symbol,
          JSON.stringify({ language }),
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
        return `trace_symbol failed (exit ${exitCode})\n\n${stderr || stdout}`
      }
    } catch (e) {
      return `Failed to run uv: ${e instanceof Error ? e.message : String(e)}\n\nMake sure 'uv' is installed and in PATH.`
    }

    let result: { files?: string[]; count?: number; error?: string; trace?: string; options?: { language?: string } }
    try {
      result = JSON.parse(stdout.trim())
    } catch {
      return `Could not parse output:\n${stdout}\n\nStderr:\n${stderr}`
    }

    if (result.error) {
      return `Error:\n${result.error}${result.trace ? `\n\nTraceback:\n${result.trace}` : ""}`
    }

    if (!result.files || result.files.length === 0) {
      return `No files found for symbol '${args.symbol}' in '${args.file}'`
    }

    const lines = result.files.map((f, i) => `  ${i + 1}. ${f}`)

    return [
      `Trace: \`${args.symbol}\``,
      `Entry: ${path.relative(workspace, resolvedFile)}`,
      `Language: ${language}`,
      `Files found: ${result.count}`,
      "",
      lines.join("\n"),
      "",
      "Use scan_module or read these files to get their content.",
    ].join("\n")
  },
})
