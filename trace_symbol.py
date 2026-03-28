#!/usr/bin/env python3
"""
trace_symbol.py — Traza archivos relacionados a un método usando resolución de tipos.

Algoritmo (como "ir a la definición" en un IDE):
    1. Encuentra el método en el archivo de entrada
    2. Para cada llamada receiver.method() dentro del método:
       a. Resuelve el TIPO del receiver leyendo los campos de la clase
       b. Va al archivo que define ese tipo
       c. Busca el método ahí
       d. Repite recursivamente
    3. Para interfaces: busca la clase que la implementa (implements NombreInterfaz)
    4. STOP cuando el tipo no está en el workspace (es externo)

Sin falsos positivos — solo sigue tipos reales, no nombres de métodos genéricos.
"""

import sys
import json
from pathlib import Path
from collections import deque

import tree_sitter_java as tsjava
import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser, Node

JAVA_LANG = Language(tsjava.language())
TS_LANG = Language(tsts.language_typescript())
TSX_LANG = Language(tsts.language_tsx())

# JS y JSX usan los mismos parsers que TS y TSX
SUPPORTED = {
    ".java": JAVA_LANG,
    ".ts": TS_LANG,
    ".tsx": TSX_LANG,
    ".js": TS_LANG,
    ".jsx": TSX_LANG,
}
SKIP_DIRS = {
    "node_modules",
    ".git",
    "target",
    "build",
    "dist",
    ".next",
    "__pycache__",
    ".gradle",
    ".idea",
    "out",
    ".cache",
}

# Tipos del lenguaje/framework a ignorar — nunca están en el workspace
JAVA_SKIP_TYPES = {
    "String",
    "Integer",
    "Long",
    "Double",
    "Float",
    "Boolean",
    "Byte",
    "List",
    "Map",
    "Set",
    "Optional",
    "Collection",
    "ArrayList",
    "HashMap",
    "HashSet",
    "LinkedList",
    "Object",
    "Class",
    "Enum",
    "Record",
    "void",
    "int",
    "long",
    "double",
    "float",
    "boolean",
    "byte",
    "char",
    "LocalDate",
    "LocalDateTime",
    "Instant",
    "Duration",
    "ZonedDateTime",
    "BigDecimal",
    "BigInteger",
    "UUID",
    # Spring
    "MongoTemplate",
    "MongoClient",
    "RedisTemplate",
    "RestTemplate",
    "ResponseEntity",
    "HttpHeaders",
    "HttpStatus",
    "HttpMethod",
    "Authentication",
    "UserDetails",
    "SecurityContext",
    # Lombok / misc
    "Builder",
    "Logger",
    "Slf4j",
}

TS_SKIP_TYPES = {
    "string",
    "number",
    "boolean",
    "void",
    "null",
    "undefined",
    "any",
    "Promise",
    "Array",
    "Record",
    "Partial",
    "Required",
    "Readonly",
    "Request",
    "Response",
    "Headers",
    "URL",
    "FormData",
    "URLSearchParams",
    "Error",
    "Date",
    "Map",
    "Set",
    "Object",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────


def node_text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def walk(root: Node, *types: str) -> list[Node]:
    results, q = [], deque([root])
    while q:
        n = q.popleft()
        if n.type in types:
            results.append(n)
        q.extend(n.children)
    return results


def parse(path: Path):
    lang = SUPPORTED.get(path.suffix.lower())
    if not lang:
        return None, None
    try:
        src = path.read_bytes()
        return Parser(lang).parse(src), src
    except Exception:
        return None, None


def is_in_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
        return True
    except ValueError:
        return False


def should_skip(path: Path) -> bool:
    return any(skip in path.parts for skip in SKIP_DIRS)


# ─── Índice: tipo → archivo que lo define ─────────────────────────────────────


def build_type_index(workspace: Path) -> dict[str, Path]:
    """
    Índice: nombre_de_clase_o_interfaz → archivo que la define.
    Un tipo tiene exactamente UN archivo de definición (salvo colisiones de nombre).
    Prioriza: clases concretas > interfaces > otros.
    """
    index: dict[str, Path] = {}

    for path in workspace.rglob("*"):
        if path.suffix.lower() not in SUPPORTED:
            continue
        if should_skip(path):
            continue

        tree, src = parse(path)
        if not tree:
            continue

        ext = path.suffix.lower()

        if ext == ".java":
            for node in walk(
                tree.root_node,
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
            ):
                for child in node.children:
                    if child.type == "identifier":
                        name = node_text(child, src)
                        # No sobreescribir si ya hay una clase concreta
                        if name not in index:
                            index[name] = path
                        elif node.type == "class_declaration":
                            # La clase concreta tiene prioridad sobre la interfaz
                            index[name] = path
                        break
        else:
            for node in walk(
                tree.root_node,
                "function_declaration",
                "class_declaration",
                "interface_declaration",
                "type_alias_declaration",
            ):
                for child in node.children:
                    if child.type == "identifier":
                        name = node_text(child, src)
                        if name not in index:
                            index[name] = path
                        break
            # export const fn = ...
            for node in walk(tree.root_node, "export_statement"):
                for decl in walk(node, "variable_declarator"):
                    name_node = decl.child_by_field_name("name")
                    if name_node:
                        name = node_text(name_node, src)
                        if name not in index:
                            index[name] = path

    return index


# ─── Índice: interfaz → clase que la implementa ───────────────────────────────


def build_impl_index(workspace: Path) -> dict[str, Path]:
    """
    Índice: nombre_interfaz → clase concreta que la implementa.
    Busca 'implements NombreInterfaz' en clases Java.
    """
    index: dict[str, Path] = {}

    for path in workspace.rglob("*.java"):
        if should_skip(path):
            continue

        tree, src = parse(path)
        if not tree:
            continue

        for cls in walk(tree.root_node, "class_declaration"):
            # Buscar super_interfaces (implements)
            for child in cls.children:
                if child.type == "super_interfaces":
                    for iface in walk(child, "type_identifier"):
                        iface_name = node_text(iface, src)
                        if iface_name not in index:
                            index[iface_name] = path

    return index


# ─── Encontrar método en un archivo ───────────────────────────────────────────


def find_method(path: Path, tree, src: bytes, symbol: str) -> Node | None:
    ext = path.suffix.lower()

    if ext == ".java":
        # method_declaration
        for node in walk(tree.root_node, "method_declaration"):
            for child in node.children:
                if child.type == "identifier" and node_text(child, src) == symbol:
                    return node
        # abstract method en interfaces (sin cuerpo)
        for node in walk(tree.root_node, "interface_method_declaration"):
            for child in node.children:
                if child.type == "identifier" and node_text(child, src) == symbol:
                    return node
    else:
        # export async function loader / export function action
        for export in walk(tree.root_node, "export_statement"):
            for fn in walk(export, "function_declaration"):
                for child in fn.children:
                    if child.type == "identifier" and node_text(child, src) == symbol:
                        return fn
        # function declaration (sin export)
        for node in walk(tree.root_node, "function_declaration"):
            for child in node.children:
                if child.type == "identifier" and node_text(child, src) == symbol:
                    return node
        # method definition (clases)
        for node in walk(tree.root_node, "method_definition"):
            for child in node.children:
                if (
                    child.type in ("property_identifier", "identifier")
                    and node_text(child, src) == symbol
                ):
                    return node
        # export const symbol = () => ...
        for node in walk(tree.root_node, "lexical_declaration", "variable_declaration"):
            for decl in walk(node, "variable_declarator"):
                name_node = decl.child_by_field_name("name")
                if name_node and node_text(name_node, src) == symbol:
                    return node

    return None


# ─── Extraer campos de una clase Java (nombre → tipo) ─────────────────────────


def extract_fields_java(tree, src: bytes) -> dict[str, str]:
    """
    Retorna mapa: nombre_campo → tipo
    Ej: { "rootGetUserRepository": "RootGetUserRepository", "userMapper": "UserMapper" }
    """
    fields: dict[str, str] = {}

    for cls in walk(tree.root_node, "class_declaration"):
        for field in walk(cls, "field_declaration"):
            ftype = None
            fnames = []
            for child in field.children:
                if child.type == "type_identifier":
                    ftype = node_text(child, src)
                elif child.type == "generic_type":
                    # List<Foo> → ignorar el genérico, tomar el tipo base
                    for c in child.children:
                        if c.type == "type_identifier":
                            ftype = node_text(c, src)
                            break
                elif child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        fnames.append(node_text(name_node, src))
            if ftype:
                for n in fnames:
                    fields[n] = ftype

    return fields


# ─── Extraer llamadas dentro de un método Java ────────────────────────────────


def extract_references_java(
    method_node: Node, tree, src: bytes
) -> list[tuple[str, str]]:
    """
    Retorna lista de (tipo_receptor, nombre_método) para cada llamada en el método.
    Ej: [("RootGetUserRepository", "findUserByIdRoot"), ("UserMapper", "toUserRootResponse")]

    También retorna tipos de variables locales declaradas.
    """
    refs: list[tuple[str, str]] = []
    fields = extract_fields_java(tree, src)

    # Variables locales declaradas en el método: tipo nombreVar = ...
    local_vars: dict[str, str] = {}
    for decl in walk(method_node, "local_variable_declaration"):
        ltype = None
        lnames = []
        for child in decl.children:
            if child.type == "type_identifier":
                ltype = node_text(child, src)
            elif child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node:
                    lnames.append(node_text(name_node, src))
        if ltype:
            for n in lnames:
                local_vars[n] = ltype

    all_vars = {**fields, **local_vars}

    for inv in walk(method_node, "method_invocation"):
        children = list(inv.children)
        if len(children) >= 3 and children[1].type == ".":
            receiver = node_text(children[0], src)
            method_called = None
            for child in children[2:]:
                if child.type == "identifier":
                    method_called = node_text(child, src)
                    break

            receiver_type = all_vars.get(receiver)
            if receiver_type and method_called:
                refs.append((receiver_type, method_called))
        else:
            # Llamada directa — puede ser un método de la misma clase
            method_name = None
            for child in children:
                if child.type == "identifier":
                    method_name = node_text(child, src)
                    break
            if method_name:
                refs.append(("__self__", method_name))

    # new MiClase(...) → registrar el tipo creado
    for creation in walk(method_node, "object_creation_expression"):
        for child in creation.children:
            if child.type == "type_identifier":
                refs.append(("__new__", node_text(child, src)))
                break

    return refs


# ─── Extraer referencias en TypeScript ────────────────────────────────────────


def extract_imports_ts(tree, src: bytes) -> dict[str, str]:
    """
    Retorna mapa: nombre_símbolo → ruta_del_módulo
    Ej: { "fetchToGraphql": "~/api/graphql.server", "requireUserToken": "~/api/auth.server" }
    """
    imports: dict[str, str] = {}

    for imp in walk(tree.root_node, "import_statement"):
        module_path = None
        names = []
        for child in imp.children:
            if child.type == "string":
                module_path = node_text(child, src).strip("'\"` ")
            elif child.type == "import_clause":
                for sub in walk(
                    child, "identifier", "shorthand_property_identifier_pattern"
                ):
                    names.append(node_text(sub, src))
        if module_path:
            for name in names:
                imports[name] = module_path

    return imports


def resolve_ts_path(module_path: str, from_file: Path, workspace: Path) -> Path | None:
    """Resuelve una ruta de import TypeScript a un Path absoluto."""
    # Ignorar externos
    if not module_path.startswith(".") and not module_path.startswith("~/"):
        return None

    base = from_file.parent

    if module_path.startswith("~/"):
        # Alias ~ → buscar app/ o src/ en el workspace
        for candidate_base in [workspace / "app", workspace / "src", workspace]:
            if candidate_base.exists():
                base = candidate_base
                module_path = module_path[2:]
                break

    candidate_base = (base / module_path).resolve()

    # Si el path ya existe tal cual (import con extensión explícita)
    if candidate_base.exists() and is_in_workspace(candidate_base, workspace):
        return candidate_base

    for ext in [".ts", ".tsx", ".js", ".jsx"]:
        # Usar str concatenation para NO reemplazar sufijos existentes
        # auth.server + .ts → auth.server.ts (correcto)
        # auth.server.with_suffix(".ts") → auth.ts (incorrecto)
        candidate = Path(str(candidate_base) + ext)
        if candidate.exists() and is_in_workspace(candidate, workspace):
            return candidate

    for ext in [".ts", ".tsx", ".js", ".jsx"]:
        candidate = candidate_base / f"index{ext}"
        if candidate.exists() and is_in_workspace(candidate, workspace):
            return candidate

    return None


def extract_references_ts(
    method_node: Node, tree, src: bytes, from_file: Path, workspace: Path
) -> list[tuple[Path, str]]:
    """
    Retorna lista de (archivo, símbolo) referenciados desde el método TypeScript.
    Resuelve imports del archivo y busca las funciones/hooks llamados.
    """
    imports = extract_imports_ts(tree, src)
    called: set[str] = set()

    for call in walk(method_node, "call_expression"):
        fn = call.child_by_field_name("function")
        if not fn:
            continue
        if fn.type == "identifier":
            called.add(node_text(fn, src))
        elif fn.type == "member_expression":
            prop = fn.child_by_field_name("property")
            if prop:
                called.add(node_text(prop, src))

    resolved: list[tuple[Path, str]] = []
    for sym in called:
        module_path = imports.get(sym)
        if not module_path:
            continue
        path = resolve_ts_path(module_path, from_file, workspace)
        if path:
            resolved.append((path, sym))

    return resolved


# ─── BFS principal ────────────────────────────────────────────────────────────


def trace(workspace_str: str, file_str: str, symbol: str) -> list[str]:
    workspace = Path(workspace_str).resolve()
    start_file = Path(file_str).resolve()

    if not start_file.exists():
        return [f"ERROR: File not found: {file_str}"]

    # Construir índices
    type_index = build_type_index(workspace)  # tipo → archivo que lo define
    impl_index = build_impl_index(workspace)  # interfaz → implementación

    visited_files: list[Path] = []
    visited_set: set[Path] = set()
    visited_pairs: set[tuple[Path, str]] = set()

    # Cola: (archivo, método a buscar en ese archivo)
    queue: deque[tuple[Path, str]] = deque()
    queue.append((start_file, symbol))

    while queue:
        current_file, current_symbol = queue.popleft()

        pair = (current_file, current_symbol)
        if pair in visited_pairs:
            continue
        visited_pairs.add(pair)

        # Registrar archivo
        if current_file not in visited_set:
            visited_set.add(current_file)
            visited_files.append(current_file)

        tree, src = parse(current_file)
        if not tree:
            continue

        ext = current_file.suffix.lower()

        # Encontrar el método
        method_node = find_method(current_file, tree, src, current_symbol)
        if not method_node:
            continue

        if ext == ".java":
            refs = extract_references_java(method_node, tree, src)

            for receiver_type, method_called in refs:
                if receiver_type == "__new__":
                    # new MiClase() → ir al constructor
                    if method_called in JAVA_SKIP_TYPES:
                        continue
                    target = type_index.get(method_called)
                    if target and is_in_workspace(target, workspace):
                        queue.append((target, method_called))
                    continue

                if receiver_type == "__self__":
                    # Llamada a método de la misma clase
                    queue.append((current_file, method_called))
                    continue

                if receiver_type in JAVA_SKIP_TYPES:
                    continue

                # 1. Ir al archivo que define el tipo (interfaz o clase)
                target_file = type_index.get(receiver_type)
                if not target_file or not is_in_workspace(target_file, workspace):
                    continue

                # Agregar la interfaz/clase al resultado y buscar el método ahí
                queue.append((target_file, method_called))

                # 2. Si el tipo es una interfaz, también ir a la implementación
                impl_file = impl_index.get(receiver_type)
                if impl_file and is_in_workspace(impl_file, workspace):
                    queue.append((impl_file, method_called))

        else:
            # TypeScript
            resolved_refs = extract_references_ts(
                method_node, tree, src, current_file, workspace
            )

            for ref_file, ref_sym in resolved_refs:
                # Registrar el archivo si es nuevo
                if ref_file not in visited_set:
                    visited_set.add(ref_file)
                    visited_files.append(ref_file)
                # Encolar el símbolo para continuar el BFS dentro de ese archivo
                queue.append((ref_file, ref_sym))

    return [str(f.relative_to(workspace)) for f in visited_files]


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(
            json.dumps({"error": "Usage: trace_symbol.py <workspace> <file> <symbol>"})
        )
        sys.exit(1)

    try:
        result = trace(sys.argv[1], sys.argv[2], sys.argv[3])
        print(json.dumps({"files": result, "count": len(result)}))
    except Exception as e:
        import traceback

        print(json.dumps({"error": str(e), "trace": traceback.format_exc()}))
        sys.exit(1)
