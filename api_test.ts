/**
 * api_test — Custom Tool para testing manual de APIs
 *
 * Soporta dos modos:
 *   - graphql: ejecuta queries/mutations contra /graphql
 *   - rest:    ejecuta llamadas REST contra cualquier endpoint
 *
 * Autenticación:
 *   El token se obtiene automáticamente via POST /auth/login usando las
 *   credenciales TEST_USERNAME / TEST_PASSWORD del .env del proyecto activo.
 *   Si no existen esas variables, las pide como argumento.
 *
 * BACKEND_URL se resuelve en este orden:
 *   1. Variable de entorno BACKEND_URL del proceso
 *   2. Leer el .env del proyecto activo (context.directory)
 *   3. Fallback: http://localhost:8080
 */

import * as fs from "node:fs/promises";
import * as path from "node:path";
import { tool } from "@opencode-ai/plugin";

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function readEnvFile(directory: string): Promise<Record<string, string>> {
	const vars: Record<string, string> = {};
	// Busca .env en el proyecto activo y en el back/ si existe
	const candidates = [
		path.join(directory, ".env"),
		path.join(directory, "back", ".env"),
		path.join(directory, "front", ".env"),
	];
	for (const envPath of candidates) {
		try {
			const content = await fs.readFile(envPath, "utf8");
			for (const line of content.split("\n")) {
				const trimmed = line.trim();
				if (!trimmed || trimmed.startsWith("#")) continue;
				const eq = trimmed.indexOf("=");
				if (eq === -1) continue;
				const key = trimmed.slice(0, eq).trim();
				const value = trimmed.slice(eq + 1).trim();
				vars[key] = value;
			}
		} catch {
			// archivo no existe — continuar
		}
	}
	return vars;
}

async function getBackendUrl(directory: string): Promise<string> {
	if (process.env.BACKEND_URL) return process.env.BACKEND_URL;
	const env = await readEnvFile(directory);
	return env.BACKEND_URL ?? "http://localhost:8080";
}

async function getToken(
	backendUrl: string,
	directory: string,
	username?: string,
	password?: string,
): Promise<string> {
	// Intentar obtener credenciales del .env
	if (!username || !password) {
		const env = await readEnvFile(directory);
		username = username ?? env.TEST_USERNAME;
		password = password ?? env.TEST_PASSWORD;
	}

	if (!username || !password) {
		return "SIN_TOKEN — agregá TEST_USERNAME y TEST_PASSWORD a tu .env o pasalos como argumento";
	}

	const res = await fetch(`${backendUrl}/auth/login`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ username, password }),
	});

	const data = (await res.json()) as Record<string, unknown>;

	if (!res.ok || data.code === 401 || data.code === 404) {
		throw new Error(`Login fallido: ${data.message ?? res.statusText}`);
	}

	return data.token as string;
}

function formatResponse(status: number, data: unknown): string {
	const statusEmoji =
		status >= 200 && status < 300 ? "✅" : status >= 400 ? "❌" : "⚠️";
	const body = typeof data === "string" ? data : JSON.stringify(data, null, 2);
	return `${statusEmoji} Status: ${status}\n\n\`\`\`json\n${body}\n\`\`\``;
}

// ─── Tool Definition ─────────────────────────────────────────────────────────

export default tool({
	description: `Test REST and GraphQL APIs of the local backend.
Automatically logs in with TEST_USERNAME / TEST_PASSWORD from the project .env to get a JWT token.
Use for manual testing during development — no files saved, result shown inline.

Backend URL is resolved in this order:
  1. BACKEND_URL env var from the process
  2. BACKEND_URL in the project .env (also checks back/.env and front/.env)
  3. Fallback: http://localhost:8080

Configure in your project .env:
  BACKEND_URL=http://localhost:8080
  TEST_USERNAME=your_user
  TEST_PASSWORD=your_password

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
				"Override username for login (default: TEST_USERNAME from .env)",
			),

		password: tool.schema
			.string()
			.optional()
			.describe(
				"Override password for login (default: TEST_PASSWORD from .env)",
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

		// 1. Resolver backend URL
		const backendUrl = await getBackendUrl(directory);

		// 2. Obtener token (salvo que se indique skipAuth)
		let token: string | null = null;
		if (!skipAuth) {
			token = await getToken(backendUrl, directory, username, password);
		}

		const authHeader: Record<string, string> = token
			? { Authorization: `Bearer ${token}` }
			: {};

		// ─── GraphQL ───────────────────────────────────────────────────
		if (type === "graphql") {
			if (!query) return "❌ Falta el argumento `query` para tipo graphql";

			const res = await fetch(`${backendUrl}/graphql`, {
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

			const url = `${backendUrl}${endpointPath}`;

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
