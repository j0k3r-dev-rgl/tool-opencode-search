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
