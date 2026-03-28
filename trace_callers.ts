/**
 * trace_callers — Reverse trace: who calls/references a symbol.
 *
 * V1 default: direct incoming callers/references.
 * V2 optional: recursive reverse traversal + probable entry points.
 * V3 optional: impact classification (direct/indirect/public/interface chain).
 *
 * Language-aware: only loads the tree-sitter deps needed for the selected language.
 */

import { tool } from "@opencode-ai/plugin"
import * as path from "node:path"
import * as fs from "node:fs/promises"

const SCRIPT = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "trace_callers.py",
)

type CallerMatch = {
  file: string
  line: number
  column: number
  caller: string
  relation: string
  receiverType?: string
  snippet?: string
}

type ProbableEntryPoint = {
  file: string
  symbol: string
  reasons: string[]
  probable?: boolean
  depth?: number
  pathFromTarget?: Array<{ file: string; symbol: string }>
}

type RecursiveNode = {
  file: string
  symbol: string
  depth: number
}

type ClassifiedCaller = {
  file: string
  symbol: string
  caller: string
  depth: number
  line: number
  column: number
  relation: string
  receiverType?: string
  snippet?: string
  calls: { file: string; symbol: string }
  pathFromTarget?: Array<{ file: string; symbol: string }>
}

type InterfaceChain = {
  kind: string
  probable: boolean
  interface?: { name?: string; file?: string | null; symbol?: string }
  implementation?: { file: string; symbol: string }
  implementations?: Array<{ file: string }>
  callers?: ClassifiedCaller[]
}

/**
 * Build the uv --with flags based on the language selection.
 * Only loads what is needed to avoid unnecessary downloads.
 */
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
  description: `Trace incoming callers/references of a symbol using AST analysis (tree-sitter).

Given a file and symbol, scans the workspace for files that reference or call that symbol.
Returns direct incoming matches useful for impact analysis.

Supports language selection to optimize execution: only loads the parser needed.

Default mode (V1): direct callers/references.
Optional recursive mode (V2/V3): reverse traversal, probable entry points, impact classification.

Supports: Java, TypeScript/TSX, JavaScript/JSX.

Examples:
  trace_callers(file="back/.../RootGetUserUseCase.java", symbol="getUserByIdRoot", language="java")
  trace_callers(file="front/app/routes/titular/titular.tsx", symbol="loader", language="ts")
  trace_callers(file="...", symbol="...", recursive=true, maxDepth=4)`,

  args: {
    file: tool.schema
      .string()
      .describe("Path to the file containing the target symbol."),

    symbol: tool.schema
      .string()
      .describe("Exact name of the method or function to reverse-trace."),

    language: tool.schema
      .enum(["auto", "java", "ts", "typescript"])
      .optional()
      .describe("Language to analyze. 'auto' loads all parsers. 'java' or 'ts'/'typescript' loads only what is needed. Defaults to 'auto'."),

    recursive: tool.schema
      .boolean()
      .optional()
      .describe("When true, recursively follows callers and returns reverse traversal plus impact classifications."),

    maxDepth: tool.schema
      .number()
      .int()
      .min(1)
      .optional()
      .describe("Maximum recursive depth. Defaults to 3."),
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
      return `trace_callers.py not found at: ${SCRIPT}`
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
          JSON.stringify({
            recursive: args.recursive ?? false,
            maxDepth: args.maxDepth ?? 3,
            language,
          }),
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
        return `trace_callers failed (exit ${exitCode})\n\n${stderr || stdout}`
      }
    } catch (e) {
      return `Failed to run uv: ${e instanceof Error ? e.message : String(e)}\n\nMake sure 'uv' is installed and in PATH.`
    }

    let result: {
      callers?: CallerMatch[]
      count?: number
      directSummary?: { count?: number; byRelation?: Record<string, number> }
      error?: string
      mode?: string
      options?: { recursive?: boolean; maxDepth?: number; language?: string }
      recursiveResult?: {
        maxDepth?: number
        maxDepthObserved?: number
        nodeCount?: number
        edgeCount?: number
        pathCount?: number
        cycles?: Array<{ from: string; to: string; path: string[] }>
        truncated?: RecursiveNode[]
        probableEntryPoints?: ProbableEntryPoint[]
        paths?: Array<Array<RecursiveNode>>
        classifications?: {
          summary?: {
            directCallerCount?: number
            indirectCallerCount?: number
            probablePublicEntryPointCount?: number
            implementationInterfaceChainCount?: number
          }
          directCallers?: ClassifiedCaller[]
          indirectCallers?: ClassifiedCaller[]
          probablePublicEntryPoints?: ProbableEntryPoint[]
          implementationInterfaceChain?: InterfaceChain[]
        }
      }
      trace?: string
      target?: { file: string; symbol: string }
    }
    try {
      result = JSON.parse(stdout.trim())
    } catch {
      return `Could not parse output:\n${stdout}\n\nStderr:\n${stderr}`
    }

    if (result.error) {
      return `Error:\n${result.error}${result.trace ? `\n\nTraceback:\n${result.trace}` : ""}`
    }

    // --- V1 non-recursive output ---
    if (!args.recursive) {
      if (!result.callers || result.callers.length === 0) {
        return [
          `Reverse trace: \`${args.symbol}\``,
          `Target: ${path.relative(workspace, resolvedFile)}`,
          `Language: ${language}`,
          "No external direct callers/references found.",
        ].join("\n")
      }

      const lines = result.callers.map((caller, index) => {
        const location = `${caller.file}:${caller.line}`
        const ctx = caller.caller ? ` in ${caller.caller}` : ""
        const snippet = caller.snippet ? `\n     > ${caller.snippet}` : ""
        return `  ${index + 1}. ${location}${ctx} [${caller.relation}]${snippet}`
      })

      return [
        `Reverse trace: \`${args.symbol}\``,
        `Target: ${path.relative(workspace, resolvedFile)}`,
        `Language: ${language}`,
        `Incoming matches: ${result.count}`,
        "",
        lines.join("\n"),
        "",
        "Use read or scan_module on the caller files for impact analysis.",
      ].join("\n")
    }

    // --- V2/V3 recursive output ---
    const callerLines = (result.callers ?? []).map((caller, index) => {
      const location = `${caller.file}:${caller.line}`
      const ctx = caller.caller ? ` in ${caller.caller}` : ""
      const snippet = caller.snippet ? `\n     > ${caller.snippet}` : ""
      return `  ${index + 1}. ${location}${ctx} [${caller.relation}]${snippet}`
    })

    const rec = result.recursiveResult
    const cls = rec?.classifications
    const summary = cls?.summary

    const directSummary = result.directSummary?.byRelation
      ? Object.entries(result.directSummary.byRelation)
          .map(([rel, count]) => `${rel}: ${count}`)
          .join(", ")
      : "none"

    const pathLines = (rec?.paths ?? []).slice(0, 8).map((nodes, i) => {
      const rendered = nodes.map((n) => `${n.file}::${n.symbol}`).join(" <- ")
      return `  ${i + 1}. ${rendered}`
    })

    const entryLines = (cls?.probablePublicEntryPoints ?? rec?.probableEntryPoints ?? [])
      .slice(0, 8).map((e, i) => {
        const depth = e.depth ? `, depth=${e.depth}` : ""
        return `  ${i + 1}. ${e.file} :: ${e.symbol}${depth} [probable: ${e.reasons.join(", ")}]`
      })

    const cycleLines = (rec?.cycles ?? []).slice(0, 5).map((c, i) =>
      `  ${i + 1}. ${c.path.join(" <- ")}`)

    const truncLines = (rec?.truncated ?? []).slice(0, 5).map((n, i) =>
      `  ${i + 1}. ${n.file} :: ${n.symbol} (depth ${n.depth})`)

    const directCls = (cls?.directCallers ?? []).slice(0, 8).map((c, i) => {
      const receiver = c.receiverType ? `, receiver=${c.receiverType}` : ""
      return `  ${i + 1}. ${c.file}:${c.line} in ${c.symbol} [${c.relation}${receiver}]`
    })

    const indirectCls = (cls?.indirectCallers ?? []).slice(0, 8).map((c, i) => {
      const target = `${c.calls.file}::${c.calls.symbol}`
      return `  ${i + 1}. depth=${c.depth} ${c.file}:${c.line} in ${c.symbol} -> ${target} [${c.relation}]`
    })

    const chainLines = (cls?.implementationInterfaceChain ?? []).slice(0, 6).map((ch, i) => {
      const ifaceName = ch.interface?.name ?? ch.interface?.file ?? "unknown"
      const impl = ch.implementation
        ? `${ch.implementation.file}::${ch.implementation.symbol}`
        : (ch.implementations ?? []).map((x) => x.file).join(", ") || "unknown"
      return `  ${i + 1}. ${ch.kind} :: ${ifaceName} -> ${impl} (probable, callers=${ch.callers?.length ?? 0})`
    })

    return [
      `Reverse trace: \`${args.symbol}\``,
      `Target: ${path.relative(workspace, resolvedFile)}`,
      `Language: ${language}`,
      `Incoming matches: ${result.count}`,
      `Direct summary: ${directSummary}`,
      summary
        ? `Impact: direct=${summary.directCallerCount ?? 0}, indirect=${summary.indirectCallerCount ?? 0}, probable-entry=${summary.probablePublicEntryPointCount ?? 0}, interface-chain=${summary.implementationInterfaceChainCount ?? 0}`
        : "",
      "",
      "Direct callers",
      callerLines.join("\n"),
      directCls.length > 0 ? ["\nClassified direct callers", directCls.join("\n")].join("\n") : "",
      indirectCls.length > 0 ? ["\nIndirect callers", indirectCls.join("\n")].join("\n") : "",
      chainLines.length > 0 ? ["\nProbable implementation/interface chains", chainLines.join("\n")].join("\n") : "",
      "",
      `Recursion: nodes=${rec?.nodeCount ?? 0}, edges=${rec?.edgeCount ?? 0}, paths=${rec?.pathCount ?? 0}, maxDepth=${rec?.maxDepthObserved ?? 0}/${rec?.maxDepth ?? args.maxDepth ?? 3}`,
      pathLines.length > 0 ? ["\nReverse paths", pathLines.join("\n")].join("\n") : "",
      entryLines.length > 0 ? ["\nProbable public entry points", entryLines.join("\n")].join("\n") : "\nProbable public entry points\n  None detected.",
      cycleLines.length > 0 ? ["\nCycles", cycleLines.join("\n")].join("\n") : "",
      truncLines.length > 0 ? ["\nDepth-limited nodes", truncLines.join("\n")].join("\n") : "",
      "",
      "Use read or scan_module on the caller files for impact analysis.",
    ].filter(Boolean).join("\n")
  },
})
