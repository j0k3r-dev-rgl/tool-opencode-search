# OpenCode Custom Tools

Tools disponibles para el agente. Se cargan automáticamente al iniciar OpenCode.

---

## `trace_symbol` — Trazador de dependencias por símbolo

La tool más poderosa del set. Dado un archivo y el nombre de un método o función, **traza recursivamente todos los archivos del proyecto que están relacionados** — desde el entry point hasta el último adapter o función hoja.

Funciona como el "ir a la definición" de un IDE, pero automatizado y recursivo.

### Cómo funciona

Usa **tree-sitter** para parsear el AST real del código (no regex). Para cada llamada dentro del método:

**Java:**
1. Resuelve el **tipo estático** del receptor (`private final RootGetUserRepository repo` → tipo es `RootGetUserRepository`)
2. Va al archivo que define ese tipo
3. Si es una interfaz, también va a la clase que la implementa (`implements`)
4. Repite recursivamente hasta llegar a los adapters finales

**TypeScript / JavaScript:**
1. Resuelve los **imports** del archivo para saber de dónde viene cada función llamada
2. Sigue la ruta del módulo importado
3. Repite recursivamente

### Lenguajes soportados

| Extensión | Lenguaje |
|-----------|----------|
| `.java` | Java |
| `.ts` | TypeScript |
| `.tsx` | TypeScript + JSX |
| `.js` | JavaScript |
| `.jsx` | JavaScript + JSX |

### Requisitos

- `uv` instalado (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Los paquetes Python se descargan automáticamente con `uv run --with` al primer uso y quedan cacheados

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `file` | string | Archivo de entrada (absoluto o relativo al workspace) |
| `symbol` | string | Nombre exacto del método o función a trazar |

### Ejemplos

```
# Trazar un endpoint GraphQL de Java
trace_symbol(
  file="back/src/main/java/.../RootUserGraphQLController.java",
  symbol="getUserByIdRoot"
)

# Trazar un loader de React Router 7
trace_symbol(
  file="front/app/routes/titular/titular.tsx",
  symbol="loader"
)

# Trazar una action
trace_symbol(
  file="front/app/routes/titular/edit_titular.tsx",
  symbol="action"
)

# Trazar desde un use case directamente
trace_symbol(
  file="back/src/main/java/.../RootGetUserUseCase.java",
  symbol="getUserByIdRoot"
)
```

### Output de ejemplo

```
🔍 Trace: `getUserByIdRoot`
📄 Entry: back/src/.../RootUserGraphQLController.java
📁 Files found: 8

  1. com/.../RootUserGraphQLController.java
  2. com/.../RootGetUser.java
  3. com/.../RootGetUserUseCase.java
  4. com/.../UserRootResponseDTO.java
  5. com/.../RootGetUserRepository.java
  6. com/.../RootGetUserAdapter.java
  7. com/.../UserMapper.java
  8. com/.../UserQueryMongoSupport.java

💡 Use scan_module or read these files to get their content.
```

### Flujo recomendado

```
1. trace_symbol  → obtener lista de archivos relacionados
2. scan_module   → leer el contenido de esos archivos
3. El agente trabaja con contexto completo y preciso
```

### Seguridad

La tool está sandboxeada — solo puede acceder a archivos dentro del workspace activo. Cualquier path fuera del workspace retorna un error de acceso denegado.

---

## `trace_callers` — Reverse trace de callers/referencias

La contraparte práctica de `trace_symbol`. Dado un archivo y un símbolo, busca **quién lo llama o referencia desde afuera**.

`trace_callers` ahora conserva **V1 como comportamiento por defecto** y agrega capacidades V2/V3 detrás de parámetros opcionales.

### V1 default (sin flags)

Sigue optimizada para WORKING FIRST:

- prioriza **matches directos**
- devuelve **archivo caller + línea + contexto**
- evita prometer una resolución perfecta en casos dinámicos

Si llamas la tool solo con `file + symbol`, se mantiene este comportamiento.

### V2/V3 opcional (parametrizada sobre la misma tool)

#### V2.1 — Recursive reverse tree

- `recursive: true` habilita reverse traversal recursivo
- `maxDepth` limita la profundidad
- construye un árbol/grafo inverso desde el símbolo objetivo hacia sus callers
- deduplica nodos y corta ciclos de forma segura

#### V2.2 — Probable entry-point detection

Cuando `recursive=true`, también resume entry points probables con heurísticas honestas:

**Java**
- `@RestController`
- `@Controller`
- `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping`
- `@QueryMapping`, `@MutationMapping`

**TS / TSX / JS / JSX**
- archivos bajo `routes/`
- símbolo `loader`
- símbolo `action`
- archivos bajo `routes/api/`

#### V2.3 — Improved summaries

Con recursion habilitada, el output humano agrega:

- resumen de callers directos
- resumen de reverse paths
- probable entry points
- información de ciclos o depth limit si aplica

#### V3 — Impact classification buckets

Cuando `recursive=true`, el JSON recursivo agrega buckets explícitos para ayudar a agentes a tomar decisiones de refactor/impacto SIN fingir certeza:

- `directCallers`: matches entrantes a **depth 1**
- `indirectCallers`: matches recursivos a **depth > 1**
- `probablePublicEntryPoints`: entry points **probables** con depth y path resumido
- `implementationInterfaceChain`: cadenas Java probables cuando hay dispatch vía interfaz o relación interfaz/implementación

Notas de honestidad:

- `public entry points` se etiquetan como **probables** porque salen de heurísticas
- `implementation/interface chain` resume relaciones estáticas útiles, NO garantiza dispatch runtime en casos más dinámicos
- la clasificación reutiliza la misma señal del análisis base; no inventa edges nuevos

### Cómo funciona

Usa **tree-sitter** para recorrer el AST del workspace y detectar referencias entrantes directas.

**Java:**
1. Resuelve el tipo estático del receiver en llamadas `receiver.method()`
2. Si el tipo apunta al archivo objetivo, registra el caller
3. También intenta detectar dispatch vía interfaz cuando la implementación objetivo es conocida

**TypeScript / JavaScript:**
1. Resuelve imports que apuntan al archivo objetivo
2. Detecta llamadas al símbolo importado (incluyendo alias, default y namespace imports)
3. También detecta referencias directas no-invocación cuando son estáticamente claras
   (por ejemplo: asignación/pasaje de valor, return, uso en expresiones, object value,
   acceso namespace como valor, `new` y uso JSX/component)
4. Retorna caller file + línea + snippet

### Parámetros

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `file` | string | Archivo donde vive el símbolo objetivo |
| `symbol` | string | Nombre exacto del método o función |
| `recursive` | boolean | Opcional. Si es `true`, sigue callers recursivamente |
| `maxDepth` | number | Opcional. Profundidad máxima cuando `recursive=true` (default: `3`) |

No se agregaron parámetros nuevos para V3. La evolución vive en el output.

### Ejemplos

```
trace_callers(
  file="back/src/main/java/.../RootGetUserUseCase.java",
  symbol="getUserByIdRoot"
)

trace_callers(
  file="front/app/api/users/get-user.query.server.ts",
  symbol="getUserById"
)

trace_callers(
  file="front/app/api/users/get-user.query.server.ts",
  symbol="getUserById",
  recursive=true,
  maxDepth=4
)
```

### Output de ejemplo

```
🔁 Reverse trace: `getUserById`
📄 Target: front/app/api/users/get-user.query.server.ts
📥 Incoming matches: 2

  1. front/app/routes/users/user.tsx:14 in loader [ts.imported_call]
     ↳ const result = await getUserById(token, params.id as string)
  2. front/app/routes/users/edit.tsx:22 in action [ts.imported_call]
     ↳ const result = await getUserById(token, formData.get("id") as string)
```

Con `recursive=true`, además del árbol/path V2, el resultado JSON incluye algo de esta forma:

```json
{
  "recursiveResult": {
    "classifications": {
      "summary": {
        "directCallerCount": 2,
        "indirectCallerCount": 3,
        "probablePublicEntryPointCount": 2,
        "implementationInterfaceChainCount": 1
      },
      "directCallers": [
        {
          "file": "front/app/routes/titular/titular.tsx",
          "symbol": "loader",
          "depth": 1,
          "relation": "ts.imported_call",
          "calls": {
            "file": "front/app/api/modulos/titular.query.service.ts",
            "symbol": "getTitularById"
          }
        }
      ],
      "indirectCallers": [],
      "probablePublicEntryPoints": [
        {
          "file": "front/app/routes/titular/titular.tsx",
          "symbol": "loader",
          "probable": true,
          "reasons": ["ts.route_file", "ts.loader_symbol"]
        }
      ],
      "implementationInterfaceChain": []
    }
  }
}
```

### Limitaciones V1

- Se enfoca en **callers/referencias directas**, no en reverse traversal recursivo completo
- Java sigue centrado en **invocaciones directas** e interface-dispatch resoluble; referencias no-call en Java todavía no se prometen por señal insuficiente
- TypeScript/JavaScript evita contextos inseguros o de tipos para mantener falsos positivos bajos, así que puede omitir aliasing complejo, re-exports/barrels complejos o casos dinámicos
- Prefiere **accuracy > ambition**

### Notas V2

- El reverse recursion reutiliza el resultado directo de V1 como bloque base; NO intenta adivinar más allá de lo que la señal permite
- Los entry points son **probables**, no garantizados
- Si un caller cae en `<module>`, se conserva como nodo hoja útil pero no se fuerza una resolución ficticia hacia arriba
- `maxDepth` protege contra explosión combinatoria y recorridos interminables

### Notas V3

- `directCallers` e `indirectCallers` están pensados para **impact analysis**, no para reemplazar una call graph formal
- `pathFromTarget` es un resumen del camino más corto conocido desde el target hasta ese match dentro del traversal actual
- En Java, `receiverType` se conserva cuando ayuda a explicar un chain de interfaz/implementación
- `implementationInterfaceChain` puede incluir:
  - `java.interface_dispatch_chain`
  - `java.target_implements_interface`
  - `java.interface_has_implementations`

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
# Ver estructura de un módulo
scan_module(path="back/src/main/java/.../modules/titular", mode="tree")

# Leer todos los adapters de un módulo
scan_module(path="back/src/main/java/.../modules/user", mode="read", filePattern="*Adapter*", extensions=[".java"])

# Buscar archivos que usen MongoIdUtils
scan_module(path="back/src", mode="read", search="MongoIdUtils", extensions=[".java"])

# Leer routes del front con límite de líneas
scan_module(path="front/app/routes", mode="read", extensions=[".tsx"], maxLines=50)
```

### Seguridad

Sandboxeada al workspace activo — no puede escanear fuera del directorio de trabajo.

---

## `api_test` — Tester de APIs

Ejecuta requests contra el backend local (REST o GraphQL) con autenticación automática.

### Autenticación

Lee `TEST_USERNAME` y `TEST_PASSWORD` del `.env` del proyecto. Hace `POST /auth/login` automáticamente y usa el token en cada request.

```env
TEST_USERNAME=tu_usuario
TEST_PASSWORD=tu_password
```

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
# GraphQL query
api_test(type="graphql", query="{ getTitularById(id: \"abc\") { data { names } } }")

# REST GET
api_test(type="rest", method="GET", path="/titulares?page=0&size=10")

# REST POST
api_test(type="rest", method="POST", path="/titulares", body="{\"names\":\"Juan\"}")

# Sin auth (login público)
api_test(type="rest", method="POST", path="/auth/login", skipAuth=true, body="{\"username\":\"x\",\"password\":\"y\"}")
```
