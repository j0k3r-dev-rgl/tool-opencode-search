/**
 * api_test — Custom Tool para testing manual de APIs
 *
 * Soporta dos modos:
 *   - graphql: ejecuta queries/mutations contra /graphql
 *   - rest:    ejecuta llamadas REST contra cualquier endpoint
 *
 * Autenticación:
 *   El token se obtiene automáticamente via POST /auth/login usando las
 *   credenciales TEST_USERNAME / TEST_PASSWORD del .env.tool del proyecto.
 *
 * Config se lee exclusivamente de .env.tool en la raíz del proyecto:
 *   BACKEND_URL=http://localhost:8080
 *   TEST_USERNAME=your_user
 *   TEST_PASSWORD=your_password
 */

import * as fs from "node:fs/promises";
import * as path from "node:path";
import { tool } from "@opencode-ai/plugin";

// ─── Helpers ─────────────────────────────────────────────────────────────────

const ENV_TOOL_FILE = ".env.tool"
const ENV_TOOL_REQUIRED_VARS = ["BACKEND_URL", "TEST_USERNAME", "TEST_PASSWORD"]
const ENV_TOOL_DEFAULT_USERNAME_FIELD = "username"
const ENV_TOOL_DEFAULT_PASSWORD_FIELD = "password"

interface EnvToolResult {
	vars: Record<string, string>
	missingFile: boolean
	missingVars: string[]
}

async function readEnvTool(directory: string): Promise<EnvToolResult> {
	const envPath = path.join(directory, ENV_TOOL_FILE)
	const vars: Record<string, string> = {}

	try {
		const content = await fs.readFile(envPath, "utf8")
		for (const line of content.split("\n")) {
			const trimmed = line.trim()
			if (!trimmed || trimmed.startsWith("#")) continue
			const eq = trimmed.indexOf("=")
			if (eq === -1) continue
			const key = trimmed.slice(0, eq).trim()
			const value = trimmed.slice(eq + 1).trim()
			vars[key] = value
		}
	} catch {
		return { vars: {}, missingFile: true, missingVars: ENV_TOOL_REQUIRED_VARS }
	}

	const missingVars = ENV_TOOL_REQUIRED_VARS.filter((v) => !vars[v])
	return { vars, missingFile: false, missingVars }
}

function buildSetupError(directory: string, missingFile: boolean, missingVars: string[]): string {
	const envPath = path.join(directory, ENV_TOOL_FILE)
	if (missingFile) {
		return [
			`❌ Missing config file: ${envPath}`,
			``,
			`Create it with the following content:`,
			``,
			`  BACKEND_URL=http://localhost:8080`,
			`  TEST_USERNAME=your_user`,
			`  TEST_PASSWORD=your_password`,
			``,
			`  # Optional — login field names (defaults: username / password)`,
			`  # TEST_USERNAME_FIELD=email`,
			`  # TEST_PASSWORD_FIELD=password`,
		].join("\n")
	}
	return [
		`❌ Missing variables in ${envPath}:`,
		``,
		...missingVars.map((v) => `  ${v}=<value>`),
		``,
		`Add the missing variables to the file and try again.`,
	].join("\n")
}

async function getToken(
	backendUrl: string,
	username: string,
	password: string,
	usernameField: string,
	passwordField: string,
): Promise<string> {
	const res = await fetch(`${backendUrl}/auth/login`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ [usernameField]: username, [passwordField]: password }),
	})

	const data = (await res.json()) as Record<string, unknown>

	if (!res.ok || data.code === 401 || data.code === 404) {
		throw new Error(`Login failed: ${data.message ?? res.statusText}`)
	}

	return data.token as string
}

function formatResponse(status: number, data: unknown): string {
	const statusEmoji =
		status >= 200 && status < 300 ? "✅" : status >= 400 ? "❌" : "⚠️";
	const body = typeof data === "string" ? data : JSON.stringify(data, null, 2);
	return `${statusEmoji} Status: ${status}\n\n\`\`\`json\n${body}\n\`\`\``;
}

// ─── Tool Definition ─────────────────────────────────────────────────────────

export default tool({
	description: `Test REST and GraphQL APIs of the local backend (http://localhost:8080).
Automatically logs in with TEST_USERNAME / TEST_PASSWORD from the project .env.tool to get a JWT token.
Use for manual testing during development — no files saved, result shown inline.

Requires a .env.tool file at the project root with:
  BACKEND_URL=http://localhost:8080
  TEST_USERNAME=your_user
  TEST_PASSWORD=your_password

  # Optional — login field names (defaults: username / password)
  # TEST_USERNAME_FIELD=email
  # TEST_PASSWORD_FIELD=password

If the file is missing or any variable is absent, the tool reports exactly what to add and where.

Examples:
- GraphQL query:  type=graphql, query="{ getTitularById(id: \\"abc\\") { data { names } responseStatus { code } } }"
- REST GET:       type=rest, method=GET, path=/titulares?page=0&size=10
- REST POST:      type=rest, method=POST, path=/titulares, body={"names":"Juan"}
- No auth:        type=rest, method=POST, path=/auth/login, skipAuth=true, body={"username":"x","password":"y"}`,

	args: {
		type: tool.schema
			.enum(["graphql", "rest"])
			.describe(
				"Type of request: 'graphql' for GraphQL queries/mutations, 'rest' for REST endpoints",
			),

		// GraphQL
		query: tool.schema
			.string()
			.optional()
			.describe(
				"GraphQL query or mutation string (required when type=graphql)",
			),

		// REST
		method: tool.schema
			.enum(["GET", "POST", "PUT", "PATCH", "DELETE"])
			.optional()
			.describe("HTTP method (required when type=rest)"),

		path: tool.schema
			.string()
			.optional()
			.describe(
				"Endpoint path without base URL, e.g. /titulares or /titulares?page=0&size=10 (required when type=rest)",
			),

		body: tool.schema
			.string()
			.optional()
			.describe("JSON body as string for POST/PUT/PATCH requests"),

		// Auth overrides
		skipAuth: tool.schema
			.boolean()
			.optional()
			.describe(
				"Skip authentication — use for public endpoints like /auth/login",
			),

		username: tool.schema
			.string()
			.optional()
			.describe(
				"Override username for login (default: TEST_USERNAME from .env.tool)",
			),

		password: tool.schema
			.string()
			.optional()
			.describe(
				"Override password for login (default: TEST_PASSWORD from .env.tool)",
			),
	},

	async execute(args, context) {
		const {
			type,
			query,
			method,
			path: endpointPath,
			body,
			skipAuth,
			username,
			password,
		} = args;
		const directory = context.directory ?? process.cwd();

		// 1. Leer .env.tool — fuente única de configuración
		const envTool = await readEnvTool(directory)

		// Credenciales: args tienen prioridad sobre .env.tool
		const resolvedUsername = username ?? envTool.vars.TEST_USERNAME
		const resolvedPassword = password ?? envTool.vars.TEST_PASSWORD
		const resolvedBackendUrl = envTool.vars.BACKEND_URL ?? "http://localhost:8080"
		const resolvedUsernameField = envTool.vars.TEST_USERNAME_FIELD ?? ENV_TOOL_DEFAULT_USERNAME_FIELD
		const resolvedPasswordField = envTool.vars.TEST_PASSWORD_FIELD ?? ENV_TOOL_DEFAULT_PASSWORD_FIELD

		// 2. Validar config si se necesita auth
		if (!skipAuth) {
			const missing: string[] = []
			if (envTool.missingFile) {
				return buildSetupError(directory, true, [])
			}
			if (!resolvedUsername) missing.push("TEST_USERNAME")
			if (!resolvedPassword) missing.push("TEST_PASSWORD")
			if (missing.length > 0) {
				return buildSetupError(directory, false, missing)
			}
		}

		// 3. Obtener token (salvo que se indique skipAuth)
		let token: string | null = null;
		if (!skipAuth) {
			token = await getToken(resolvedBackendUrl, resolvedUsername!, resolvedPassword!, resolvedUsernameField, resolvedPasswordField);
		}

		const authHeader: Record<string, string> = token
			? { Authorization: `Bearer ${token}` }
			: {};

		// ─── GraphQL ───────────────────────────────────────────────────
		if (type === "graphql") {
			if (!query) return "❌ Falta el argumento `query` para tipo graphql";

			const res = await fetch(`${resolvedBackendUrl}/graphql`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
					...authHeader,
				},
				body: JSON.stringify({ query }),
			});

		type GraphQLError = { message: string; extensions?: { classification?: string } };
		type GraphQLResponse = { data?: unknown; errors?: GraphQLError[] };

		const data = (await res.json()) as GraphQLResponse;

		// Detectar errores GraphQL
		if (data.errors && data.errors.length > 0) {
			const errors = data.errors
				.map(
					(e) =>
						`  - ${e.message}${e.extensions?.classification ? ` [${e.extensions.classification}]` : ""}`,
				)
				.join("\n");
			return `❌ GraphQL Errors (HTTP ${res.status}):\n${errors}\n\n\`\`\`json\n${JSON.stringify(data, null, 2)}\n\`\`\``;
		}

		return formatResponse(res.status, data.data ?? data);
		}

		// ─── REST ──────────────────────────────────────────────────────
		if (type === "rest") {
			if (!method) return "❌ Falta el argumento `method` para tipo rest";
			if (!endpointPath) return "❌ Falta el argumento `path` para tipo rest";

			const url = `${resolvedBackendUrl}${endpointPath}`;

			const hasBody = ["POST", "PUT", "PATCH"].includes(method) && body;
			let parsedBody: string | undefined;
			if (hasBody) {
				// Validar que sea JSON válido
				try {
					JSON.parse(body!);
					parsedBody = body;
				} catch {
					return `❌ El body no es JSON válido:\n${body}`;
				}
			}

			const res = await fetch(url, {
				method,
				headers: {
					"Content-Type": "application/json",
					...authHeader,
				},
				body: parsedBody,
			});

			let data: unknown;
			const contentType = res.headers.get("content-type") ?? "";
			if (contentType.includes("application/json")) {
				data = await res.json();
			} else {
				data = await res.text();
			}

			return formatResponse(res.status, data);
		}

		return "❌ Tipo inválido — usar 'graphql' o 'rest'";
	},
});
