/**
 * grep_workspace — Search text or regex across workspace files.
 *
 * Returns matches grouped by file with optional surrounding context lines.
 * Much cheaper than scan_module for content search — returns only the
 * matching lines and their context, not the full file content.
 */

import { tool } from "@opencode-ai/plugin"
import * as fs from "node:fs/promises"
import * as path from "node:path"

// ─── Constants ────────────────────────────────────────────────────────────────

const DEFAULT_EXCLUDE = [
  "node_modules", ".git", "target", "build", "dist",
  ".next", ".cache", "__pycache__", ".gradle", ".idea",
  ".ruff_cache",
]

const BINARY_EXTENSIONS = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
  ".pdf", ".zip", ".tar", ".gz", ".jar", ".war", ".class",
  ".lock", ".woff", ".woff2", ".ttf", ".eot",
])

// ─── Types ────────────────────────────────────────────────────────────────────

interface LineMatch {
  line: number
  content: string
  isMatch: boolean   // true = matched line, false = context line
}

interface FileMatches {
  file: string
  matchCount: number
  lines: LineMatch[]
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function shouldExclude(name: string, excludeList: string[]): boolean {
  return excludeList.some((pattern) => {
    if (pattern.startsWith("*.")) return name.endsWith(pattern.slice(1))
    return name === pattern
  })
}

async function walkFiles(
  dirPath: string,
  options: {
    extensions?: string[]
    exclude: string[]
    maxDepth?: number
  },
  currentDepth = 0,
): Promise<string[]> {
  const results: string[] = []

  if (options.maxDepth !== undefined && currentDepth > options.maxDepth) {
    return results
  }

  let entries: import("node:fs").Dirent[]
  try {
    entries = await fs.readdir(dirPath, { withFileTypes: true })
  } catch {
    return results
  }

  for (const entry of entries) {
    if (shouldExclude(entry.name, options.exclude)) continue

    const fullPath = path.join(dirPath, entry.name)

    if (entry.isDirectory()) {
      const sub = await walkFiles(fullPath, options, currentDepth + 1)
      results.push(...sub)
      continue
    }

    if (!entry.isFile()) continue

    const ext = path.extname(entry.name).toLowerCase()
    if (BINARY_EXTENSIONS.has(ext)) continue

    if (options.extensions && options.extensions.length > 0) {
      const normalized = options.extensions.map((e) => (e.startsWith(".") ? e : `.${e}`))
      if (!normalized.includes(ext)) continue
    }

    results.push(fullPath)
  }

  return results
}

function buildContextLines(
  fileLines: string[],
  matchedRows: Set<number>,
  contextLines: number,
): LineMatch[] {
  if (contextLines === 0) {
    return Array.from(matchedRows)
      .sort((a, b) => a - b)
      .map((row) => ({
        line: row + 1,
        content: fileLines[row],
        isMatch: true,
      }))
  }

  // Expand context around each match
  const included = new Set<number>()
  for (const row of matchedRows) {
    for (let i = Math.max(0, row - contextLines); i <= Math.min(fileLines.length - 1, row + contextLines); i++) {
      included.add(i)
    }
  }

  const sortedRows = Array.from(included).sort((a, b) => a - b)
  const result: LineMatch[] = []
  let prevRow = -2

  for (const row of sortedRows) {
    // Add separator marker as a gap indicator when rows are not consecutive
    if (prevRow >= 0 && row > prevRow + 1) {
      result.push({ line: -1, content: "---", isMatch: false })
    }
    result.push({
      line: row + 1,
      content: fileLines[row],
      isMatch: matchedRows.has(row),
    })
    prevRow = row
  }

  return result
}

function searchInContent(
  content: string,
  pattern: RegExp,
): Set<number> {
  const lines = content.split("\n")
  const matched = new Set<number>()
  for (let i = 0; i < lines.length; i++) {
    pattern.lastIndex = 0
    if (pattern.test(lines[i])) {
      matched.add(i)
    }
  }
  return matched
}

function formatOutput(
  fileMatches: FileMatches[],
  totalMatchCount: number,
  totalFileCount: number,
  pattern: string,
  isRegex: boolean,
): string {
  const header = [
    `Pattern: ${isRegex ? "/" : ""}${pattern}${isRegex ? "/" : ""}`,
    `Matches: ${totalMatchCount} in ${totalFileCount} file${totalFileCount !== 1 ? "s" : ""}`,
  ].join("\n")

  const body = fileMatches
    .map((fm) => {
      const fileHeader = `\n${fm.file}  (${fm.matchCount} match${fm.matchCount !== 1 ? "es" : ""})`
      const lineBlocks = fm.lines.map((l) => {
        if (l.line === -1) return "  ..."
        const prefix = l.isMatch ? ">" : " "
        return `  ${prefix} ${String(l.line).padStart(4)}:  ${l.content}`
      })
      return [fileHeader, ...lineBlocks].join("\n")
    })
    .join("\n")

  return `${header}\n${body}`
}

// ─── Tool Definition ─────────────────────────────────────────────────────────

export default tool({
  description: `Search text or regex across workspace files — returns matches grouped by file with context lines.

Much cheaper than scan_module for content search: returns only matching lines and context,
not full file content. Ideal for finding usages, annotations, imports, or any text pattern.

Examples:
  grep_workspace(pattern="@QueryMapping", extensions=[".java"])
  grep_workspace(pattern="useSession", extensions=[".ts", ".tsx"], context=2)
  grep_workspace(pattern="MongoIdUtils", extensions=[".java"], context=1)
  grep_workspace(pattern="import.*useSession", extensions=[".ts", ".tsx"], regex=true)
  grep_workspace(pattern="CreateTitularRequest", context=0)
  grep_workspace(pattern="\\buseSession\\b", extensions=[".ts", ".tsx"], regex=true)`,

  args: {
    pattern: tool.schema
      .string()
      .describe("Text or regex pattern to search for. Plain text matches substrings — if you need exact word match (e.g. 'useSession' without matching 'useSessionCountdown'), use regex=true with word boundaries: '\\buseSession\\b'."),

    extensions: tool.schema
      .array(tool.schema.string())
      .optional()
      .describe("Only search files with these extensions. E.g. [\".java\"], [\".ts\", \".tsx\"]. Omit to search all text files."),

    regex: tool.schema
      .boolean()
      .optional()
      .describe("If true, treat pattern as a regular expression. Use this with \\b for exact word boundaries (e.g. '\\buseSession\\b') or for complex patterns. Default: false (plain text, case-insensitive)."),

    context: tool.schema
      .number()
      .optional()
      .describe("Number of surrounding lines to show around each match. Default: 2. Use 0 for match-only output."),

    exclude: tool.schema
      .array(tool.schema.string())
      .optional()
      .describe("Additional folder names or *.ext patterns to exclude from the search."),

    maxDepth: tool.schema
      .number()
      .optional()
      .describe("Maximum directory depth to recurse into. Omit for unlimited depth."),

    maxMatchesPerFile: tool.schema
      .number()
      .optional()
      .describe("Stop collecting matches after this many per file. Useful for noisy patterns. Default: unlimited."),
  },

  async execute(args, context) {
    const workspace = context.directory ?? process.cwd()
    const resolvedWorkspace = path.resolve(workspace)

    const contextLines = args.context ?? 2
    const isRegex = args.regex ?? false
    const maxMatchesPerFile = args.maxMatchesPerFile

    // Build the search regex
    let searchPattern: RegExp
    try {
      if (isRegex) {
        searchPattern = new RegExp(args.pattern, "g")
      } else {
        // Plain text: escape and make case-insensitive
        const escaped = args.pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
        searchPattern = new RegExp(escaped, "gi")
      }
    } catch (e) {
      return `Invalid regex pattern: ${e instanceof Error ? e.message : String(e)}`
    }

    // Collect files
    const excludeList = [...DEFAULT_EXCLUDE, ...(args.exclude ?? [])]
    const allFiles = await walkFiles(resolvedWorkspace, {
      extensions: args.extensions,
      exclude: excludeList,
      maxDepth: args.maxDepth,
    })

    if (allFiles.length === 0) {
      return `No files found to search in '${workspace}' with the given filters.`
    }

    const fileMatches: FileMatches[] = []
    let totalMatchCount = 0

    for (const filePath of allFiles) {
      let content: string
      try {
        content = await fs.readFile(filePath, "utf8")
      } catch {
        continue
      }

      const matchedRows = searchInContent(content, searchPattern)
      if (matchedRows.size === 0) continue

      const fileLines = content.split("\n")
      let effectiveRows = matchedRows

      // Apply per-file match cap
      if (maxMatchesPerFile !== undefined && matchedRows.size > maxMatchesPerFile) {
        const capped = Array.from(matchedRows).sort((a, b) => a - b).slice(0, maxMatchesPerFile)
        effectiveRows = new Set(capped)
      }

      const contextLinesList = buildContextLines(fileLines, effectiveRows, contextLines)
      const relPath = path.relative(resolvedWorkspace, filePath)

      fileMatches.push({
        file: relPath,
        matchCount: effectiveRows.size,
        lines: contextLinesList,
      })

      totalMatchCount += effectiveRows.size
    }

    if (fileMatches.length === 0) {
      return `No matches found for '${args.pattern}'.`
    }

    return formatOutput(fileMatches, totalMatchCount, fileMatches.length, args.pattern, isRegex)
  },
})
