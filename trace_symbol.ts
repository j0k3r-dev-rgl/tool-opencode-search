/**
 * trace_symbol — Traza el árbol completo de archivos relacionados a un método/función.
 *
 * Dado un archivo y un nombre de método, sigue todas las referencias hacia adentro
 * (llamadas, interfaces, implementaciones) usando tree-sitter para parseo real del AST.
 * Retorna la lista ordenada de archivos involucrados, desde el entry point hasta los adapters.
 *
 * Soporta: Java, TypeScript, TSX
 * Requiere: uv + tree-sitter instalado
 */

import { tool } from "@opencode-ai/plugin"
import * as path from "node:path"
import * as fs from "node:fs/promises"

const SCRIPT = path.join(
  path.dirname(new URL(import.meta.url).pathname),
  "trace_symbol.py",
)

export default tool({
  description: `Trace all files related to a method or function using AST analysis (tree-sitter).

Given a file and a method name, recursively follows all internal references
(calls, interface implementations, injected dependencies) and returns the
complete list of project files involved — from the entry point down to the
final adapter or leaf function.

Skips: standard library calls, framework methods, external packages.
Supports: Java (Spring hexagonal), TypeScript/TSX/JavaScript/JSX (React Router 7).

Use this before reading a feature — get the file list first, then read only what you need.

Examples:
  trace_symbol(file="back/src/main/java/.../RootUserGraphQLController.java", symbol="getUserByIdRoot")
  trace_symbol(file="front/app/routes/titular/titular.tsx", symbol="loader")
  trace_symbol(file="back/src/main/java/.../RootGetUserUseCase.java", symbol="getUsers")`,

  args: {
    file: tool.schema
      .string()
      .describe("Path to the file containing the method. Can be absolute or relative to workspace."),

    symbol: tool.schema
      .string()
      .describe("Exact name of the method or function to trace."),
  },

  async execute(args, context) {
    const workspace = context.directory ?? process.cwd()

    // Resolver path absoluto del archivo
    const filePath = path.isAbsolute(args.file)
      ? args.file
      : path.join(workspace, args.file)

    // Sandbox: verificar que el archivo está dentro del workspace
    const resolvedFile = path.resolve(filePath)
    const resolvedWorkspace = path.resolve(workspace)
    if (!resolvedFile.startsWith(resolvedWorkspace + path.sep) && resolvedFile !== resolvedWorkspace) {
      return `❌ Access denied: '${args.file}' is outside the current workspace`
    }

    // Verificar que el archivo existe
    try {
      await fs.access(resolvedFile)
    } catch {
      return `❌ File not found: '${args.file}'`
    }

    // Verificar que el script Python existe
    try {
      await fs.access(SCRIPT)
    } catch {
      return `❌ trace_symbol.py not found at: ${SCRIPT}`
    }

    // Ejecutar con uv (descarga tree-sitter on-demand, cachea automáticamente)
    let stdout = ""
    let stderr = ""

    try {
      const proc = Bun.spawn(
        [
          "uv", "run",
          "--with", "tree-sitter",
          "--with", "tree-sitter-java",
          "--with", "tree-sitter-typescript",
          "python3", SCRIPT,
          resolvedWorkspace,
          resolvedFile,
          args.symbol,
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
        return `❌ trace_symbol failed (exit ${exitCode})\n\n${stderr || stdout}`
      }
    } catch (e) {
      return `❌ Failed to run uv: ${e instanceof Error ? e.message : String(e)}\n\nMake sure 'uv' is installed and in PATH.`
    }

    // Parsear resultado JSON
    let result: { files?: string[]; count?: number; error?: string; trace?: string }
    try {
      result = JSON.parse(stdout.trim())
    } catch {
      return `❌ Could not parse output:\n${stdout}\n\nStderr:\n${stderr}`
    }

    if (result.error) {
      return `❌ Error:\n${result.error}${result.trace ? `\n\nTraceback:\n${result.trace}` : ""}`
    }

    if (!result.files || result.files.length === 0) {
      return `⚠️ No files found for symbol '${args.symbol}' in '${args.file}'`
    }

    // Formatear resultado
    const lines = result.files.map((f, i) => `  ${i + 1}. ${f}`)

    return [
      `🔍 Trace: \`${args.symbol}\``,
      `📄 Entry: ${path.relative(workspace, resolvedFile)}`,
      `📁 Files found: ${result.count}`,
      ``,
      lines.join("\n"),
      ``,
      `💡 Use scan_module or read these files to get their content.`,
    ].join("\n")
  },
})
