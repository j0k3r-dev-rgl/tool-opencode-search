<a id="english"></a>

# OpenCode Custom Tools

**Language / Idioma:** [English](#english) | [Español](#español)

Custom tools available to the agent. Loaded automatically on startup.

## Why these tools exist

These tools were created to solve a recurring problem in AI-assisted code navigation.

When an agent needs to fix a bug, understand a flow, or find the right implementation,
it often falls into an inefficient pattern: open file after file, try regex searches,
run shell commands, and keep guessing until it finds the right place.

That wastes both **time** and **tokens**.

The idea for this toolkit came from how **LSPs and IDEs** work. In an editor, you can
press **Ctrl+Click** (or equivalent) and jump directly to a definition or implementation.
That is fast, precise, and gives only the context you actually need.

So the goal of these tools is to bring that same idea to AI agents:

- jump directly to the right definition
- trace the real flow of an endpoint or feature
- inspect only the relevant module
- return compact, high-signal output instead of full file dumps

In other words: instead of forcing the agent to brute-force the codebase with regex and shell
commands until it gets lucky, we provide dedicated tools that understand common navigation tasks
and return only the minimum necessary context.

That is why this repository focuses on tools like:

- `find_symbol` → go to a definition
- `trace_symbol` / `trace_callers` → follow real code flow
- `list_endpoints` → index the API surface
- `grep_workspace` → search text with compact context
- `scan_module` → read only the relevant module

The result is a workflow that is faster, more deterministic, and much more token-efficient.

## Agent-specific tool availability

OpenCode lets you restrict tool availability **per agent** (including primary agents and subagents).
This is important because keeping fewer tools available to an agent reduces noise and can help lower context/token overhead.

The official docs support agent-level tool control via `agent.<name>.tools` (legacy but still supported) or `agent.<name>.permission`.

Example:

```json
{
  "agent": {
    "gentleman": {
      "mode": "primary",
      "tools": {
        "api_test": false,
        "trace_callers": false,
        "list_endpoints": false
      }
    },
    "sdd-explore": {
      "mode": "subagent",
      "tools": {
        "find_symbol": true,
        "grep_workspace": true,
        "list_endpoints": true,
        "api_test": false
      }
    }
  }
}
```

### Why this matters

- Keep the **primary agent** thin and strategic
- Give **exploration agents** discovery tools (`find_symbol`, `grep_workspace`, `list_endpoints`, `trace_symbol`)
- Give **verification agents** runtime tools like `api_test`
- Avoid exposing every tool to every agent when it is not needed

### Recommended approach

- Use `tools` for simple on/off filtering by tool name
- Prefer `permission` for newer configs when you need finer control
- Design agents by responsibility: exploration, implementation, verification, orchestration

This README documents the tools themselves. Availability should be configured in `opencode.json` per agent.

## Architecture

Both tracing tools share a common core to avoid logic duplication:

```
tools/
├── trace_core/                     # Shared tracing engine
│   ├── common.py                   # Parse, walk, indexes, workspace, dedup
│   ├── analyzers/
│   │   ├── java.py                 # Java: analyze_file() + extract_references()
│   │   └── typescript.py           # TS/JS: analyze_file() + extract_references()
│   ├── forward.py                  # BFS forward trace (trace_symbol)
│   ├── traversal.py                # Direct + recursive reverse (trace_callers)
│   └── classification.py           # V3 impact classification
│
├── endpoints_core/                 # Modular endpoint/route scanner
│   ├── common.py                   # Endpoint dataclass, ANALYZERS registry, file walker
│   ├── formatter.py                # Compact text output grouped by kind
│   └── analyzers/
│       ├── java.py                 # Spring: @QueryMapping, @GetMapping, etc.
│       └── typescript.py           # React Router 7: loader, action, resource routes
│
├── trace_symbol.py / .ts           # Forward trace: follow calls outward
├── trace_callers.py / .ts          # Reverse trace: find incoming callers
├── find_symbol.py / .ts            # Definition locator: find where a symbol is defined
├── list_endpoints.py / .ts         # Endpoint/route index: full API surface at a glance
├── grep_workspace.ts               # Text/regex search with context lines
├── scan_module.ts                  # Directory scanner
└── api_test.ts                     # Local API tester
```

Fixes in `trace_core` automatically apply to both tracing tools.

---

## `trace_symbol` — Forward dependency trace

Given a file and a method name, **recursively follows all internal references** (calls, interface implementations, injected dependencies) and returns the complete list of project files involved — from the entry point down to the final adapter or leaf function.

Works like IDE "go to definition", but automated and recursive.

### How it works

Uses **tree-sitter** for real AST parsing (not regex).

**Java:**
1. Resolves the **static type** of the receiver (`private final GetUserRepository repo` -> type is `GetUserRepository`)
2. Goes to the file that defines that type
3. If it is an interface, also follows the implementing class (`implements`)
4. Repeats recursively until it reaches final adapters

**TypeScript / JavaScript:**
1. Resolves **imports** from the file to know where each called function comes from
2. Follows the module path (supports `~/` alias, relative paths, extensions)
3. Repeats recursively

### Supported languages

| Extension | Language |
|-----------|----------|
| `.java` | Java |
| `.ts` | TypeScript |
| `.tsx` | TypeScript + JSX |
| `.js` | JavaScript |
| `.jsx` | JavaScript + JSX |

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | Entry file (absolute or relative to workspace) |
| `symbol` | string | Exact name of the method or function to trace |
| `language` | `auto` \| `java` \| `ts` \| `typescript` | Optional. Controls which parser is loaded. Defaults to `auto` |

### Examples

```
trace_symbol(
  file="back/src/main/java/.../TitularGraphQLController.java",
  symbol="getTitularById",
  language="java"
)

trace_symbol(
  file="front/app/routes/titular/titular.tsx",
  symbol="loader",
  language="ts"
)
```

### Example output

```
Trace: `getTitularById`
Entry: back/src/.../TitularGraphQLController.java
Language: java
Files found: 10

  1. .../TitularGraphQLController.java
  2. .../GetTitularById.java
  3. .../GetTitularByIdUseCase.java
  4. .../GetTitularByIdRepository.java
  5. .../GetTitularByIdAdapter.java
  ...

Use scan_module or read these files to get their content.
```

### Recommended flow

```
1. trace_symbol  -> get the list of related files
2. scan_module   -> read the content of those files
3. The agent works with complete and precise context
```

---

## `trace_callers` — Reverse trace of callers/references

The practical counterpart of `trace_symbol`. Given a file and a symbol, finds **who calls or references it from outside**.

Default behavior (V1) is preserved when only `file + symbol` are passed. V2/V3 capabilities are available behind optional parameters.

### V1 default (no flags)

- Finds **direct incoming matches**
- Returns **caller file + line + context**
- Does not promise perfect resolution for dynamic cases

### V2/V3 optional (parameterized on the same tool)

#### V2.1 — Recursive reverse tree

- `recursive: true` enables recursive reverse traversal
- `maxDepth` limits depth
- Builds an inverse graph from the target symbol toward its callers
- Deduplicates nodes and cuts cycles safely

#### V2.2 — Probable entry-point detection

When `recursive=true`, surfaces probable entry points with honest heuristics:

**Java**
- `@RestController`, `@Controller`
- `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping`
- `@QueryMapping`, `@MutationMapping`

**TS / TSX / JS / JSX**
- Files under `routes/`
- Symbol `loader` or `action`
- Files under `routes/api/`

#### V2.3 — Improved summaries

With recursion enabled, the output adds:
- Direct callers summary
- Reverse paths summary
- Probable entry points
- Cycle and depth-limit info if applicable

#### V3 — Impact classification

When `recursive=true`, the recursive JSON result includes classification buckets:

- `directCallers`: incoming matches at **depth 1**
- `indirectCallers`: recursive matches at **depth > 1**
- `probablePublicEntryPoints`: **probable** entry points with depth and path summary
- `implementationInterfaceChain`: probable Java chains when interface dispatch or interface/implementation relationships are involved

Honesty notes:
- Public entry points are labeled **probable** because they come from heuristics
- Interface chains reflect static signal only; dynamic dispatch can hide paths
- Classification reuses the same analysis signal; it does not invent new edges

### How it works

Uses **tree-sitter** to scan the workspace AST and detect incoming references.

**Java:**
1. Resolves the static receiver type in `receiver.method()` calls
2. If the type points to the target file, registers the caller
3. Detects dispatch via interface when the target implementation is known

**TypeScript / JavaScript:**
1. Resolves imports that point to the target file
2. Detects calls to the imported symbol (including alias, default, and namespace imports)
3. Also detects direct non-call references when statically clear
   (assignment, return, expressions, object value, namespace value, `new`, JSX/component usage)
4. Returns caller file + line + snippet

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `file` | string | File where the target symbol lives |
| `symbol` | string | Exact name of the method or function |
| `language` | `auto` \| `java` \| `ts` \| `typescript` | Optional. Controls which parser is loaded. Defaults to `auto` |
| `recursive` | boolean | Optional. If `true`, follows callers recursively |
| `maxDepth` | number | Optional. Maximum depth when `recursive=true` (default: `3`) |

### Examples

```
trace_callers(
  file="back/src/main/java/.../SendAiMessageUseCase.java",
  symbol="send",
  language="java"
)

trace_callers(
  file="front/app/providers/SessionProvider.tsx",
  symbol="useSession",
  language="ts"
)

trace_callers(
  file="front/app/providers/SessionProvider.tsx",
  symbol="useSession",
  language="ts",
  recursive=true,
  maxDepth=4
)
```

### Example output

```
Reverse trace: `useSession`
Target: front/app/providers/SessionProvider.tsx
Language: ts
Incoming matches: 9

  1. front/app/components/Header.tsx:86 in UserMenu [ts.imported_call]
     > const { session } = useSession();
  2. front/app/hooks/useSessionCountdown.ts:15 in useSessionCountdown [ts.imported_call]
     > const { session, logout } = useSession();
  ...

Use read or scan_module on the caller files for impact analysis.
```

### Limitations

- Java focuses on **direct invocations** and resolvable interface-dispatch; non-call references in Java are not promised due to insufficient signal
- TypeScript/JavaScript avoids unsafe or type-only contexts to keep false positives low, so it may miss complex aliasing, barrel re-exports, or dynamic cases
- Prefers **accuracy over ambition**

---

## `find_symbol` — Locate a symbol definition

Given a name, scans the workspace AST and returns all files + lines where that class, interface, function, method, or type is **defined**.

Use this when you know a symbol name but not its file. Much cheaper than grep — returns only `file + line + kind`, no content.

### How it works

Uses **tree-sitter** to parse each file and find declaration nodes by name.

**Java:** Finds `class_declaration`, `interface_declaration`, `enum_declaration`, `method_declaration`, `constructor_declaration`, `annotation_type_declaration`.

**TypeScript / JavaScript:** Finds `class_declaration`, `interface_declaration`, `type_alias_declaration`, `function_declaration`, `method_definition`, `enum_declaration`, and `const/let` declarations that assign arrow functions or components.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Symbol name to search for |
| `language` | `auto` \| `java` \| `ts` \| `typescript` | Optional. Limits scan to one language. Defaults to `auto` |
| `kind` | `any` \| `class` \| `interface` \| `function` \| `method` \| `type` \| `enum` \| `constructor` \| `annotation` | Optional. Filter by definition kind. Defaults to `any` |
| `fuzzy` | boolean | Optional. If `true`, match names that **contain** the search term (case-insensitive). Default: `false` |

### Examples

```
find_symbol(name="CreateTitularUseCase", language="java")

find_symbol(name="getTitularById", language="java", kind="method")

find_symbol(name="useSession", language="ts")

find_symbol(name="Titular", language="java", fuzzy=true)
```

### Example output

```
Symbol: `CreateTitularUseCase`
Matches: 1

  1. back/src/main/java/.../use_cases/command/CreateTitularUseCase.java:12  [class] CreateTitularUseCase

Use read or scan_module to inspect the file content.
```

---

## `list_endpoints` — API surface index

Scans the workspace and returns all endpoints and routes grouped by kind. Returns only name, kind, file, line, and path — **no file content**.

Use this to understand the full API surface of a project before reading individual files.

Modular: adding a new language requires only creating a new file in `endpoints_core/analyzers/` and registering it.

### Supported

| Language | Framework | What it detects |
|----------|-----------|-----------------|
| Java | Spring Boot | `@QueryMapping`, `@MutationMapping`, `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping` |
| TypeScript | React Router 7 | `loader`, `action` exports in `routes/`; resource routes under `routes/api/` |

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `language` | `auto` \| `java` \| `typescript` | Optional. Limits scan to one language. Defaults to `auto` |
| `type` | `any` \| `graphql` \| `rest` \| `routes` | Optional. Filter by endpoint type. Defaults to `any` |

### Examples

```
list_endpoints()
list_endpoints(language="java", type="graphql")
list_endpoints(language="java", type="rest")
list_endpoints(language="typescript")
```

### Example output

```
Endpoints found: 24

── Java / Spring Boot ─────────────────────────
  GraphQL Queries (12)
    TitularGraphQLController
      getTitularById        back/.../TitularGraphQLController.java:34
      getTitulares          back/.../TitularGraphQLController.java:41

  REST POST (3)
    TitularRestController
      createTitular         /titulares    back/.../TitularRestController.java:24

── TypeScript / React Router 7 ────────────────
  Loaders (8)
    titular
      loader                /titular/:id  front/app/routes/titular/titular.tsx:12

  Actions (4)
    crear
      action                /titular/crear  front/app/routes/titular/crear.tsx:18
```

### Adding a new language

Create `endpoints_core/analyzers/yourLanguage.py` implementing:

```python
def analyze(workspace: Path, options: dict) -> AnalyzerResult:
    ...

register("yourlanguage", analyze)
```

Then add the import to `endpoints_core/analyzers/__init__.py`. Nothing else changes.

---

## `grep_workspace` — Text/regex search with context

Search for text or a regex pattern across workspace files. Returns matches **grouped by file** with configurable surrounding context lines.

Much cheaper than `scan_module` for content search: returns only the matching lines and their context, not the full file content.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `pattern` | string | Text or regex pattern to search for |
| `extensions` | string[] | Optional. Only search files with these extensions |
| `regex` | boolean | Optional. Treat `pattern` as regex. Default: `false` (plain text, case-insensitive) |
| `context` | number | Optional. Surrounding lines to show around each match. Default: `2` |
| `exclude` | string[] | Optional. Additional folder names or `*.ext` patterns to exclude |
| `maxDepth` | number | Optional. Maximum directory depth |
| `maxMatchesPerFile` | number | Optional. Cap matches per file — useful for noisy patterns |

### Examples

```
grep_workspace(pattern="@QueryMapping", extensions=[".java"])

grep_workspace(pattern="useSession", extensions=[".ts", ".tsx"], context=2)

grep_workspace(pattern="MongoIdUtils", extensions=[".java"], context=1)

grep_workspace(pattern="import.*useSession", extensions=[".ts", ".tsx"], regex=true)
```

### Example output

```
Pattern: @QueryMapping
Matches: 7 in 3 files

back/.../TitularGraphQLController.java  (3 matches)
  >   23:    @QueryMapping
       24:    public SingleResponse<TitularDetailResponse> getTitularById(...)
  ...
  >   31:    @QueryMapping
       32:    public ListResponse<TitularItemResponse> getTitulares(...)
```

---

## `scan_module` — Directory scanner

Scans a project folder and returns the file structure or full content.

### Modes

| Mode | Description |
|------|-------------|
| `tree` | File structure only (lightweight, for orientation) |
| `read` | Structure + full content of each file |

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Directory to scan |
| `mode` | `tree` \| `read` | Scan mode |
| `extensions` | string[] | Filter by extension — e.g. `[".java", ".ts"]` |
| `exclude` | string[] | Extra folders/patterns to exclude |
| `filePattern` | string | Wildcard by name — e.g. `*Adapter*`, `GetUser*` |
| `maxDepth` | number | Maximum depth levels |
| `maxFileSizeKB` | number | Skip files larger than X KB |
| `maxLines` | number | Truncate files after N lines |
| `search` | string | Only include files containing this text |
| `searchRegex` | boolean | Treat `search` as regex |
| `includeStats` | boolean | Show size and lines in tree mode |

### Examples

```
scan_module(path="back/src/main/java/.../modules/titular", mode="tree")

scan_module(path="back/src/main/java/.../modules/user", mode="read", filePattern="*Adapter*", extensions=[".java"])

scan_module(path="back/src", mode="read", search="MongoIdUtils", extensions=[".java"])

scan_module(path="front/app/routes", mode="read", extensions=[".tsx"], maxLines=50)
```

---

## `api_test` — Local API tester

Executes requests against the local backend (REST or GraphQL) with automatic authentication.

### Authentication & Configuration

Reads all configuration from a `.env.tool` file at the **project root**. Does `POST /auth/login` automatically and uses the token on each request.

```env
# .env.tool — place this at your project root
BACKEND_URL=http://localhost:8080
TEST_USERNAME=your_user
TEST_PASSWORD=your_password

# Optional — login field names sent in the POST /auth/login body
# Defaults: username / password. Override if your backend uses different names (e.g. email)
# TEST_USERNAME_FIELD=email
# TEST_PASSWORD_FIELD=password
```

If the file is missing or any variable is absent, the tool reports exactly what to create or add — no guessing.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `type` | `graphql` \| `rest` | Request type |
| `query` | string | GraphQL query or mutation |
| `method` | string | HTTP method for REST |
| `path` | string | REST endpoint path |
| `body` | string | JSON body for POST/PUT/PATCH |
| `skipAuth` | boolean | Skip authentication |
| `username` | string | Username override |
| `password` | string | Password override |

### Examples

```
api_test(type="graphql", query="{ getTitularById(id: \"abc\") { data { names } } }")

api_test(type="rest", method="GET", path="/titulares?page=0&size=10")

api_test(type="rest", method="POST", path="/titulares", body="{\"names\":\"Juan\"}")

api_test(type="rest", method="POST", path="/auth/login", skipAuth=true, body="{\"username\":\"x\",\"password\":\"y\"}")
```

---

## Security

All tools are sandboxed — they can only access files within the active workspace. Any path outside the workspace returns an access denied error.

## Requirements

- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Python packages are downloaded automatically with `uv run --with` on first use and cached

---

<a id="español"></a>

# OpenCode Custom Tools (Español)

[Back to English / Volver a inglés](#english)

Herramientas personalizadas disponibles para el agente. Se cargan automáticamente al iniciar.

## Por qué existen estas tools

Estas tools nacieron para resolver un problema recurrente en la navegación de código asistida por IA.

Cuando un agente necesita corregir un bug, entender un flujo o encontrar la implementación correcta,
muchas veces cae en un patrón ineficiente: abrir archivo tras archivo, probar búsquedas por regex,
ejecutar comandos de shell, y seguir adivinando hasta encontrar el lugar correcto.

Eso desperdicia tanto **tiempo** como **tokens**.

La idea de este toolkit nació de cómo funcionan los **LSPs y los IDEs**. En un editor,
podés hacer **Ctrl+Click** (o equivalente) y saltar directo a una definición o implementación.
Eso es rápido, preciso, y entrega solo el contexto que realmente hace falta.

Entonces el objetivo de estas tools es llevar esa misma idea a los agentes de IA:

- saltar directo a la definición correcta
- trazar el flujo real de un endpoint o feature
- inspeccionar solo el módulo relevante
- devolver output compacto y de alta señal en vez de volcar archivos completos

En otras palabras: en vez de obligar al agente a brute-forcear el codebase con regex y comandos
de shell hasta acertar, se le proveen tools dedicadas que entienden tareas comunes de navegación
y retornan solo el contexto mínimo necesario.

Por eso este repositorio se enfoca en tools como:

- `find_symbol` → ir a una definición
- `trace_symbol` / `trace_callers` → seguir el flujo real del código
- `list_endpoints` → indexar la superficie de la API
- `grep_workspace` → buscar texto con contexto compacto
- `scan_module` → leer solo el módulo relevante

El resultado es un workflow más rápido, más determinístico y mucho más eficiente en tokens.

## Disponibilidad de tools por agente

OpenCode permite restringir la disponibilidad de tools **por agente** (incluyendo agentes primarios y subagentes).
Esto es importante porque mantener menos tools disponibles para un agente reduce ruido y puede ayudar a bajar el overhead de contexto/tokens.

La documentación oficial soporta control por agente mediante `agent.<nombre>.tools` (legacy pero todavía soportado) o `agent.<nombre>.permission`.

Ejemplo:

```json
{
  "agent": {
    "gentleman": {
      "mode": "primary",
      "tools": {
        "api_test": false,
        "trace_callers": false,
        "list_endpoints": false
      }
    },
    "sdd-explore": {
      "mode": "subagent",
      "tools": {
        "find_symbol": true,
        "grep_workspace": true,
        "list_endpoints": true,
        "api_test": false
      }
    }
  }
}
```

### Por qué importa

- Mantener el **agente principal** delgado y estratégico
- Dar a los **agentes de exploración** tools de descubrimiento (`find_symbol`, `grep_workspace`, `list_endpoints`, `trace_symbol`)
- Dar a los **agentes de verificación** tools de runtime como `api_test`
- Evitar exponer todas las tools a todos los agentes cuando no hace falta

### Enfoque recomendado

- Usar `tools` para filtrado simple on/off por nombre de tool
- Preferir `permission` en configs nuevas cuando se necesite control más fino
- Diseñar agentes por responsabilidad: exploración, implementación, verificación, orquestación

Este README documenta las tools en sí. La disponibilidad debe configurarse por agente en `opencode.json`.

## Arquitectura

Ambas herramientas de tracing comparten un core común para evitar duplicación de lógica:

```
tools/
├── trace_core/                     # Motor de tracing compartido
│   ├── common.py                   # Parse, walk, indexes, workspace, dedup
│   ├── analyzers/
│   │   ├── java.py                 # Java: analyze_file() + extract_references()
│   │   └── typescript.py           # TS/JS: analyze_file() + extract_references()
│   ├── forward.py                  # BFS forward trace (trace_symbol)
│   ├── traversal.py                # Direct + recursive reverse (trace_callers)
│   └── classification.py           # Clasificación de impacto V3
│
├── endpoints_core/                 # Escáner modular de endpoints/rutas
│   ├── common.py                   # Dataclass Endpoint, registro ANALYZERS, file walker
│   ├── formatter.py                # Output compacto agrupado por kind
│   └── analyzers/
│       ├── java.py                 # Spring: @QueryMapping, @GetMapping, etc.
│       └── typescript.py           # React Router 7: loader, action, resource routes
│
├── trace_symbol.py / .ts           # Forward trace: sigue llamadas hacia adentro
├── trace_callers.py / .ts          # Reverse trace: busca callers entrantes
├── find_symbol.py / .ts            # Localizador: encuentra dónde está definido un símbolo
├── list_endpoints.py / .ts         # Índice de endpoints/rutas: superficie API completa
├── grep_workspace.ts               # Búsqueda de texto/regex con contexto
├── scan_module.ts                  # Escáner de carpetas
└── api_test.ts                     # Tester de APIs local
```

Los fixes en `trace_core` se aplican automáticamente a ambas herramientas de tracing.

---

## `trace_symbol` — Traza de dependencias hacia adelante

Dado un archivo y el nombre de un método, **sigue recursivamente todas las referencias internas** (llamadas, implementaciones de interfaces, dependencias inyectadas) y retorna la lista completa de archivos del proyecto involucrados — desde el entry point hasta el último adapter o función hoja.

Funciona como "ir a la definición" de un IDE, pero automatizado y recursivo.

### Cómo funciona

Usa **tree-sitter** para parseo real del AST (no regex).

**Java:**
1. Resuelve el **tipo estático** del receptor (`private final GetUserRepository repo` -> tipo es `GetUserRepository`)
2. Va al archivo que define ese tipo
3. Si es una interfaz, también sigue la clase que la implementa (`implements`)
4. Repite recursivamente hasta llegar a los adapters finales

**TypeScript / JavaScript:**
1. Resuelve los **imports** del archivo para saber de dónde viene cada función llamada
2. Sigue la ruta del módulo (soporta alias `~/`, rutas relativas, extensiones)
3. Repite recursivamente

### Lenguajes soportados

| Extensión | Lenguaje |
|-----------|----------|
| `.java` | Java |
| `.ts` | TypeScript |
| `.tsx` | TypeScript + JSX |
| `.js` | JavaScript |
| `.jsx` | JavaScript + JSX |

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `file` | string | Archivo de entrada (absoluto o relativo al workspace) |
| `symbol` | string | Nombre exacto del método o función a trazar |
| `language` | `auto` \| `java` \| `ts` \| `typescript` | Opcional. Controla qué parser se carga. Por defecto `auto` |

### Ejemplos

```
trace_symbol(
  file="back/src/main/java/.../TitularGraphQLController.java",
  symbol="getTitularById",
  language="java"
)

trace_symbol(
  file="front/app/routes/titular/titular.tsx",
  symbol="loader",
  language="ts"
)
```

### Output de ejemplo

```
Trace: `getTitularById`
Entry: back/src/.../TitularGraphQLController.java
Language: java
Files found: 10

  1. .../TitularGraphQLController.java
  2. .../GetTitularById.java
  3. .../GetTitularByIdUseCase.java
  4. .../GetTitularByIdRepository.java
  5. .../GetTitularByIdAdapter.java
  ...

Use scan_module or read these files to get their content.
```

### Flujo recomendado

```
1. trace_symbol  -> obtener la lista de archivos relacionados
2. scan_module   -> leer el contenido de esos archivos
3. El agente trabaja con contexto completo y preciso
```

---

## `trace_callers` — Reverse trace de callers/referencias

La contraparte práctica de `trace_symbol`. Dado un archivo y un símbolo, busca **quién lo llama o referencia desde afuera**.

El comportamiento por defecto (V1) se preserva cuando solo se pasan `file + symbol`. Las capacidades V2/V3 están disponibles detrás de parámetros opcionales.

### V1 por defecto (sin flags)

- Encuentra **matches directos entrantes**
- Retorna **archivo caller + línea + contexto**
- No promete resolución perfecta en casos dinámicos

### V2/V3 opcional (parametrizado sobre la misma tool)

#### V2.1 — Árbol recursivo inverso

- `recursive: true` habilita reverse traversal recursivo
- `maxDepth` limita la profundidad
- Construye un grafo inverso desde el símbolo objetivo hacia sus callers
- Deduplica nodos y corta ciclos de forma segura

#### V2.2 — Detección de entry points probables

Cuando `recursive=true`, muestra entry points probables con heurísticas honestas:

**Java**
- `@RestController`, `@Controller`
- `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping`
- `@QueryMapping`, `@MutationMapping`

**TS / TSX / JS / JSX**
- Archivos bajo `routes/`
- Símbolo `loader` o `action`
- Archivos bajo `routes/api/`

#### V2.3 — Resúmenes mejorados

Con recursión habilitada, el output agrega:
- Resumen de callers directos
- Resumen de reverse paths
- Entry points probables
- Info de ciclos o límite de profundidad si aplica

#### V3 — Clasificación de impacto

Cuando `recursive=true`, el resultado JSON recursivo incluye buckets de clasificación:

- `directCallers`: matches entrantes a **depth 1**
- `indirectCallers`: matches recursivos a **depth > 1**
- `probablePublicEntryPoints`: entry points **probables** con depth y path resumido
- `implementationInterfaceChain`: cadenas Java probables cuando hay dispatch vía interfaz o relaciones interfaz/implementación

Notas de honestidad:
- Los entry points públicos se etiquetan como **probables** porque salen de heurísticas
- Las cadenas de interfaz reflejan solo señal estática; el dispatch dinámico puede ocultar paths
- La clasificación reutiliza la misma señal del análisis base; no inventa edges nuevos

### Cómo funciona

Usa **tree-sitter** para recorrer el AST del workspace y detectar referencias entrantes.

**Java:**
1. Resuelve el tipo estático del receptor en llamadas `receiver.method()`
2. Si el tipo apunta al archivo objetivo, registra el caller
3. Detecta dispatch vía interfaz cuando la implementación objetivo es conocida

**TypeScript / JavaScript:**
1. Resuelve imports que apuntan al archivo objetivo
2. Detecta llamadas al símbolo importado (incluyendo alias, default y namespace imports)
3. También detecta referencias directas no-invocación cuando son estáticamente claras
   (asignación, return, expresiones, object value, namespace value, `new`, uso JSX/component)
4. Retorna archivo caller + línea + snippet

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `file` | string | Archivo donde vive el símbolo objetivo |
| `symbol` | string | Nombre exacto del método o función |
| `language` | `auto` \| `java` \| `ts` \| `typescript` | Opcional. Controla qué parser se carga. Por defecto `auto` |
| `recursive` | boolean | Opcional. Si es `true`, sigue callers recursivamente |
| `maxDepth` | number | Opcional. Profundidad máxima cuando `recursive=true` (default: `3`) |

### Ejemplos

```
trace_callers(
  file="back/src/main/java/.../SendAiMessageUseCase.java",
  symbol="send",
  language="java"
)

trace_callers(
  file="front/app/providers/SessionProvider.tsx",
  symbol="useSession",
  language="ts"
)

trace_callers(
  file="front/app/providers/SessionProvider.tsx",
  symbol="useSession",
  language="ts",
  recursive=true,
  maxDepth=4
)
```

### Output de ejemplo

```
Reverse trace: `useSession`
Target: front/app/providers/SessionProvider.tsx
Language: ts
Incoming matches: 9

  1. front/app/components/Header.tsx:86 in UserMenu [ts.imported_call]
     > const { session } = useSession();
  2. front/app/hooks/useSessionCountdown.ts:15 in useSessionCountdown [ts.imported_call]
     > const { session, logout } = useSession();
  ...

Use read or scan_module on the caller files for impact analysis.
```

### Limitaciones

- Java se enfoca en **invocaciones directas** e interface-dispatch resoluble; referencias no-call en Java no se prometen por señal insuficiente
- TypeScript/JavaScript evita contextos inseguros o de solo-tipo para mantener falsos positivos bajos, así que puede omitir aliasing complejo, barrel re-exports o casos dinámicos
- Prefiere **accuracy sobre ambition**

---

## `find_symbol` — Localizar definición de un símbolo

Dado un nombre, escanea el AST del workspace y retorna todos los archivos + líneas donde esa clase, interfaz, función, método o tipo está **definido**.

Usarlo cuando se conoce el nombre de un símbolo pero no el archivo. Mucho más barato que grep — retorna solo `archivo + línea + kind`, sin contenido.

### Cómo funciona

Usa **tree-sitter** para parsear cada archivo y encontrar nodos de declaración por nombre.

**Java:** Encuentra `class_declaration`, `interface_declaration`, `enum_declaration`, `method_declaration`, `constructor_declaration`, `annotation_type_declaration`.

**TypeScript / JavaScript:** Encuentra `class_declaration`, `interface_declaration`, `type_alias_declaration`, `function_declaration`, `method_definition`, `enum_declaration`, y declaraciones `const/let` que asignan arrow functions o componentes.

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `name` | string | Nombre del símbolo a buscar |
| `language` | `auto` \| `java` \| `ts` \| `typescript` | Opcional. Limita el escaneo a un lenguaje. Por defecto `auto` |
| `kind` | `any` \| `class` \| `interface` \| `function` \| `method` \| `type` \| `enum` \| `constructor` \| `annotation` | Opcional. Filtra por tipo de definición. Por defecto `any` |
| `fuzzy` | boolean | Opcional. Si es `true`, coincide con nombres que **contienen** el término (case-insensitive). Por defecto `false` |

### Ejemplos

```
find_symbol(name="CreateTitularUseCase", language="java")

find_symbol(name="getTitularById", language="java", kind="method")

find_symbol(name="useSession", language="ts")

find_symbol(name="Titular", language="java", fuzzy=true)
```

### Output de ejemplo

```
Symbol: `CreateTitularUseCase`
Matches: 1

  1. back/src/main/java/.../use_cases/command/CreateTitularUseCase.java:12  [class] CreateTitularUseCase

Use read or scan_module to inspect the file content.
```

---

## `list_endpoints` — Índice de superficie API

Escanea el workspace y retorna todos los endpoints y rutas agrupados por kind. Retorna solo nombre, kind, archivo, línea y path — **sin contenido de archivos**.

Usar para entender la superficie completa de la API de un proyecto antes de leer archivos individuales.

Modular: agregar un nuevo lenguaje requiere solo crear un archivo en `endpoints_core/analyzers/` y registrarlo.

### Soportado

| Lenguaje | Framework | Qué detecta |
|----------|-----------|-------------|
| Java | Spring Boot | `@QueryMapping`, `@MutationMapping`, `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping` |
| TypeScript | React Router 7 | exports `loader`, `action` en `routes/`; resource routes bajo `routes/api/` |

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `language` | `auto` \| `java` \| `typescript` | Opcional. Limita el escaneo a un lenguaje. Por defecto `auto` |
| `type` | `any` \| `graphql` \| `rest` \| `routes` | Opcional. Filtra por tipo de endpoint. Por defecto `any` |

### Agregar un nuevo lenguaje

Crear `endpoints_core/analyzers/tuLenguaje.py` implementando:

```python
def analyze(workspace: Path, options: dict) -> AnalyzerResult:
    ...

register("tulenguaje", analyze)
```

Luego agregar el import en `endpoints_core/analyzers/__init__.py`. Nada más cambia.

---

## `grep_workspace` — Búsqueda de texto/regex con contexto

Busca texto o un patrón regex en los archivos del workspace. Retorna los matches **agrupados por archivo** con líneas de contexto configurables.

Mucho más barato que `scan_module` para búsqueda en contenido: retorna solo las líneas que coinciden y su contexto, no el archivo completo.

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `pattern` | string | Texto o patrón regex a buscar |
| `extensions` | string[] | Opcional. Solo buscar en archivos con estas extensiones |
| `regex` | boolean | Opcional. Tratar `pattern` como regex. Por defecto `false` (texto plano, case-insensitive) |
| `context` | number | Opcional. Líneas de contexto alrededor de cada match. Por defecto `2` |
| `exclude` | string[] | Opcional. Nombres de carpetas o patrones `*.ext` extra a excluir |
| `maxDepth` | number | Opcional. Profundidad máxima de carpetas |
| `maxMatchesPerFile` | number | Opcional. Límite de matches por archivo — útil para patrones con muchos resultados |

### Ejemplos

```
grep_workspace(pattern="@QueryMapping", extensions=[".java"])

grep_workspace(pattern="useSession", extensions=[".ts", ".tsx"], context=2)

grep_workspace(pattern="MongoIdUtils", extensions=[".java"], context=1)

grep_workspace(pattern="import.*useSession", extensions=[".ts", ".tsx"], regex=true)
```

### Output de ejemplo

```
Pattern: @QueryMapping
Matches: 7 in 3 files

back/.../TitularGraphQLController.java  (3 matches)
  >   23:    @QueryMapping
       24:    public SingleResponse<TitularDetailResponse> getTitularById(...)
  ...
  >   31:    @QueryMapping
       32:    public ListResponse<TitularItemResponse> getTitulares(...)
```

---

## `scan_module` — Escáner de carpetas

Escanea una carpeta del proyecto y retorna la estructura de archivos o el contenido completo.

### Modos

| Modo | Descripción |
|------|-------------|
| `tree` | Solo estructura de archivos (liviano, para orientarse) |
| `read` | Estructura + contenido completo de cada archivo |

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `path` | string | Carpeta a escanear |
| `mode` | `tree` \| `read` | Modo de escaneo |
| `extensions` | string[] | Filtrar por extensión — ej. `[".java", ".ts"]` |
| `exclude` | string[] | Carpetas/patrones extra a excluir |
| `filePattern` | string | Wildcard por nombre — ej. `*Adapter*`, `GetUser*` |
| `maxDepth` | number | Niveles máximos de profundidad |
| `maxFileSizeKB` | number | Ignorar archivos más grandes que X KB |
| `maxLines` | number | Truncar archivos después de N líneas |
| `search` | string | Solo incluir archivos que contengan este texto |
| `searchRegex` | boolean | Tratar `search` como regex |
| `includeStats` | boolean | Mostrar tamaño y líneas en modo tree |

### Ejemplos

```
scan_module(path="back/src/main/java/.../modules/titular", mode="tree")

scan_module(path="back/src/main/java/.../modules/user", mode="read", filePattern="*Adapter*", extensions=[".java"])

scan_module(path="back/src", mode="read", search="MongoIdUtils", extensions=[".java"])

scan_module(path="front/app/routes", mode="read", extensions=[".tsx"], maxLines=50)
```

---

## `api_test` — Tester de APIs local

Ejecuta requests contra el backend local (REST o GraphQL) con autenticación automática.

### Autenticación y configuración

Lee toda la configuración desde un archivo `.env.tool` en la **raíz del proyecto**. Hace `POST /auth/login` automáticamente y usa el token en cada request.

```env
# .env.tool — crear en la raíz del proyecto
BACKEND_URL=http://localhost:8080
TEST_USERNAME=tu_usuario
TEST_PASSWORD=tu_password

# Opcional — nombres de los campos en el body de POST /auth/login
# Por defecto: username / password. Cambiar si el backend usa otros nombres (ej. email)
# TEST_USERNAME_FIELD=email
# TEST_PASSWORD_FIELD=password
```

Si el archivo no existe o falta alguna variable, la tool reporta exactamente qué crear o agregar — sin ambigüedad.

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `type` | `graphql` \| `rest` | Tipo de request |
| `query` | string | Query o mutation GraphQL |
| `method` | string | Método HTTP para REST |
| `path` | string | Path del endpoint REST |
| `body` | string | JSON body para POST/PUT/PATCH |
| `skipAuth` | boolean | Omitir autenticación |
| `username` | string | Override de usuario |
| `password` | string | Override de password |

### Ejemplos

```
api_test(type="graphql", query="{ getTitularById(id: \"abc\") { data { names } } }")

api_test(type="rest", method="GET", path="/titulares?page=0&size=10")

api_test(type="rest", method="POST", path="/titulares", body="{\"names\":\"Juan\"}")

api_test(type="rest", method="POST", path="/auth/login", skipAuth=true, body="{\"username\":\"x\",\"password\":\"y\"}")
```

---

## Seguridad

Todas las herramientas están sandboxeadas — solo pueden acceder a archivos dentro del workspace activo. Cualquier path fuera del workspace retorna un error de acceso denegado.

## Requisitos

- `uv` instalado (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Los paquetes Python se descargan automáticamente con `uv run --with` al primer uso y quedan cacheados
