/**
 * scan_module — Custom Tool para escanear módulos/carpetas del proyecto
 *
 * Modos:
 *   - tree: solo estructura de carpetas y archivos (liviano)
 *   - read: estructura + contenido completo de cada archivo
 *
 * El agente tiene control total sobre qué incluir/excluir.
 */

import { tool } from "@opencode-ai/plugin"
import * as fs from "node:fs/promises"
import * as path from "node:path"

// ─── Types ────────────────────────────────────────────────────────────────────

interface FileEntry {
  path: string
  relativePath: string
  sizeBytes: number
  lines: number
  extension: string
  content?: string
}

interface ScanResult {
  scannedPath: string
  mode: string
  totalFiles: number
  totalSizeKB: string
  skippedFiles: number
  entries: FileEntry[]
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const DEFAULT_EXCLUDE = [
  "node_modules", ".git", "target", "build", "dist",
  ".next", ".cache", "__pycache__", ".gradle", ".idea",
  "*.class", "*.jar", "*.war", "*.lock", "bun.lock",
]

function matchesGlob(name: string, pattern: string): boolean {
  if (pattern.startsWith("*.")) {
    return name.endsWith(pattern.slice(1))
  }
  return name === pattern
}

function shouldExclude(entryName: string, excludeList: string[]): boolean {
  return excludeList.some((pattern) => matchesGlob(entryName, pattern))
}

function matchesFilePattern(name: string, pattern: string): boolean {
  // Soporta wildcards: *Adapter*, GetUser*, etc.
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&")
  const regex = new RegExp("^" + escaped.replace(/\*/g, ".*") + "$", "i")
  return regex.test(name)
}

async function walkDirectory(
  dirPath: string,
  rootPath: string,
  options: {
    mode: "tree" | "read"
    extensions?: string[]
    exclude: string[]
    filePattern?: string
    maxDepth?: number
    maxFileSizeKB?: number
    maxLines?: number
    search?: string
    searchRegex?: boolean
    includeStats: boolean
  },
  currentDepth: number = 0,
): Promise<{ entries: FileEntry[]; skipped: number }> {
  const entries: FileEntry[] = []
  let skipped = 0

  if (options.maxDepth !== undefined && currentDepth > options.maxDepth) {
    return { entries, skipped }
  }

  let dirEntries: import("node:fs").Dirent[]
  try {
    dirEntries = await fs.readdir(dirPath, { withFileTypes: true })
  } catch {
    return { entries, skipped }
  }

  // Ordenar: carpetas primero, luego archivos
  dirEntries.sort((a, b) => {
    if (a.isDirectory() && !b.isDirectory()) return -1
    if (!a.isDirectory() && b.isDirectory()) return 1
    return a.name.localeCompare(b.name)
  })

  for (const dirent of dirEntries) {
    if (shouldExclude(dirent.name, options.exclude)) continue

    const fullPath = path.join(dirPath, dirent.name)
    const relativePath = path.relative(rootPath, fullPath)

    if (dirent.isDirectory()) {
      const sub = await walkDirectory(fullPath, rootPath, options, currentDepth + 1)
      entries.push(...sub.entries)
      skipped += sub.skipped
      continue
    }

    if (!dirent.isFile()) continue

    const ext = path.extname(dirent.name)

    // Filtro por extensión
    if (options.extensions && options.extensions.length > 0) {
      const normalizedExts = options.extensions.map((e) =>
        e.startsWith(".") ? e : `.${e}`
      )
      if (!normalizedExts.includes(ext)) {
        skipped++
        continue
      }
    }

    // Filtro por nombre de archivo
    if (options.filePattern) {
      const nameWithoutExt = path.basename(dirent.name, ext)
      const fullName = dirent.name
      if (
        !matchesFilePattern(nameWithoutExt, options.filePattern) &&
        !matchesFilePattern(fullName, options.filePattern)
      ) {
        skipped++
        continue
      }
    }

    // Tamaño del archivo
    let stat: import("node:fs").Stats
    try {
      stat = await fs.stat(fullPath)
    } catch {
      skipped++
      continue
    }

    const sizeKB = stat.size / 1024

    // Filtro por tamaño máximo
    if (options.maxFileSizeKB !== undefined && sizeKB > options.maxFileSizeKB) {
      skipped++
      continue
    }

    const entry: FileEntry = {
      path: fullPath,
      relativePath,
      sizeBytes: stat.size,
      lines: 0,
      extension: ext,
    }

    // En modo read o si hay búsqueda, leer el contenido
    if (options.mode === "read" || options.search) {
      let rawContent: string
      try {
        rawContent = await fs.readFile(fullPath, "utf8")
      } catch {
        skipped++
        continue
      }

      // Filtro por búsqueda en contenido
      if (options.search) {
        let matches: boolean
        if (options.searchRegex) {
          try {
            matches = new RegExp(options.search, "m").test(rawContent)
          } catch {
            matches = rawContent.includes(options.search)
          }
        } else {
          matches = rawContent.toLowerCase().includes(options.search.toLowerCase())
        }
        if (!matches) {
          skipped++
          continue
        }
      }

      const lines = rawContent.split("\n")
      entry.lines = lines.length

      if (options.mode === "read") {
        // Aplicar límite de líneas
        const limited =
          options.maxLines !== undefined && lines.length > options.maxLines
            ? lines.slice(0, options.maxLines).join("\n") +
              `\n... [truncated — ${lines.length - options.maxLines} more lines]`
            : rawContent
        entry.content = limited
      }
    } else {
      // Solo contar líneas en modo tree sin búsqueda
      if (options.includeStats) {
        try {
          const raw = await fs.readFile(fullPath, "utf8")
          entry.lines = raw.split("\n").length
        } catch {
          entry.lines = 0
        }
      }
    }

    entries.push(entry)
  }

  return { entries, skipped }
}

function formatTree(entries: FileEntry[], includeStats: boolean): string {
  return entries
    .map((e) => {
      const sizeStr = e.sizeBytes < 1024
        ? `${e.sizeBytes}B`
        : `${(e.sizeBytes / 1024).toFixed(1)}KB`
      const stats = includeStats ? ` [${sizeStr}, ${e.lines} lines]` : ""
      return `  ${e.relativePath}${stats}`
    })
    .join("\n")
}

function formatRead(entries: FileEntry[]): string {
  return entries
    .map((e) => {
      return [
        "---",
        `path: ${e.relativePath}`,
        `content:`,
        e.content ?? "(empty)",
      ].join("\n")
    })
    .join("\n\n")
}

// ─── Tool Definition ─────────────────────────────────────────────────────────

export default tool({
  description: `Scan a directory or module — returns file tree or full file contents.

Modes:
  - tree: lightweight structure listing with optional file stats
  - read: full content of every matched file, formatted as path + content blocks

Use tree first to orient, then read for specific files or modules.

Examples:
  scan_module(path="back/src/main/java/com/sias/modules/titular", mode="tree")
  scan_module(path="back/src/main/java/com/sias/modules/titular", mode="read", extensions=[".java"])
  scan_module(path="back/src", mode="tree", maxDepth=3, includeStats=true)
  scan_module(path="back/src", mode="read", filePattern="*Adapter*", extensions=[".java"])
  scan_module(path="front/app/routes", mode="read", extensions=[".tsx", ".ts"], maxLines=80)
  scan_module(path="back/src", mode="read", search="MongoIdUtils", extensions=[".java"])
  scan_module(path="back/src", mode="read", search="@QueryMapping", searchRegex=false, extensions=[".java"])`,

  args: {
    path: tool.schema
      .string()
      .describe("Directory path to scan. Can be absolute or relative to the session working directory."),

    mode: tool.schema
      .enum(["tree", "read"])
      .describe("'tree' returns file listing only. 'read' returns full file contents."),

    extensions: tool.schema
      .array(tool.schema.string())
      .optional()
      .describe("Only include files with these extensions. E.g. [\".java\", \".ts\"]. Omit to include all files."),

    exclude: tool.schema
      .array(tool.schema.string())
      .optional()
      .describe(`Additional patterns to exclude (folders or *.ext). Defaults always excluded: ${DEFAULT_EXCLUDE.join(", ")}.`),

    filePattern: tool.schema
      .string()
      .optional()
      .describe("Wildcard pattern to filter by filename. E.g. '*Adapter*', 'GetUser*', '*UseCase*'. Case-insensitive."),

    maxDepth: tool.schema
      .number()
      .optional()
      .describe("Maximum directory depth to recurse into. Omit for unlimited depth."),

    maxFileSizeKB: tool.schema
      .number()
      .optional()
      .describe("Skip files larger than this size in KB. Useful to ignore generated or binary files."),

    maxLines: tool.schema
      .number()
      .optional()
      .describe("In read mode: truncate files longer than this many lines. Omit for full content."),

    search: tool.schema
      .string()
      .optional()
      .describe("Only include files whose content contains this string (or regex if searchRegex=true). Case-insensitive for plain text."),

    searchRegex: tool.schema
      .boolean()
      .optional()
      .describe("If true, treat `search` as a regular expression. Default: false (plain text search)."),

    includeStats: tool.schema
      .boolean()
      .optional()
      .describe("In tree mode: show file size and line count next to each file. Default: false."),
  },

  async execute(args, context) {
    const {
      path: scanPath,
      mode,
      extensions,
      exclude,
      filePattern,
      maxDepth,
      maxFileSizeKB,
      maxLines,
      search,
      searchRegex,
      includeStats,
    } = args

    const directory = context.directory ?? process.cwd()

    // Resolver path absoluto
    const absolutePath = path.isAbsolute(scanPath)
      ? scanPath
      : path.join(directory, scanPath)

    // Verificar que el path está dentro del workspace (sandbox)
    const resolvedPath = path.resolve(absolutePath)
    const resolvedWorkspace = path.resolve(directory)
    if (!resolvedPath.startsWith(resolvedWorkspace + path.sep) && resolvedPath !== resolvedWorkspace) {
      return `❌ Access denied: '${scanPath}' is outside the current workspace (${directory})`
    }

    // Verificar que existe
    try {
      const stat = await fs.stat(absolutePath)
      if (!stat.isDirectory()) {
        return `❌ '${scanPath}' is not a directory`
      }
    } catch {
      return `❌ Path not found: '${scanPath}'`
    }

    // Combinar exclusiones default + las del agente
    const excludeList = [...DEFAULT_EXCLUDE, ...(exclude ?? [])]

    const { entries, skipped } = await walkDirectory(absolutePath, absolutePath, {
      mode,
      extensions,
      exclude: excludeList,
      filePattern,
      maxDepth,
      maxFileSizeKB,
      maxLines,
      search,
      searchRegex: searchRegex ?? false,
      includeStats: includeStats ?? false,
    })

    if (entries.length === 0) {
      return `⚠️ No files found in '${scanPath}' with the given filters.\nSkipped: ${skipped} files.`
    }

    const totalSizeBytes = entries.reduce((acc, e) => acc + e.sizeBytes, 0)
    const totalSizeKB = (totalSizeBytes / 1024).toFixed(1)

    const result: ScanResult = {
      scannedPath: scanPath,
      mode,
      totalFiles: entries.length,
      totalSizeKB,
      skippedFiles: skipped,
      entries,
    }

    // Header resumen
    const header = [
      `📁 ${result.scannedPath}`,
      `Mode: ${result.mode} | Files: ${result.totalFiles} | Size: ${result.totalSizeKB}KB | Skipped: ${result.skippedFiles}`,
      search ? `Search: "${search}"${searchRegex ? " (regex)" : ""}` : null,
      filePattern ? `Pattern: ${filePattern}` : null,
      extensions ? `Extensions: ${extensions.join(", ")}` : null,
    ]
      .filter(Boolean)
      .join("\n")

    if (mode === "tree") {
      return `${header}\n\n${formatTree(entries, includeStats ?? false)}`
    }

    return `${header}\n\n${formatRead(entries)}`
  },
})
