export const IDE_RULE = `Chronicum — единая база знаний пользователя.
Когда этот MCP подключён, не ищи правила и скиллы в папках проекта.
Сначала вызови search (или list_materials). Карточку читай через get_material: summary → card → full.
Новые правила и заметки клади add_note / add_url, а не в .md репозитория.`

export function apiOrigin(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"
  return raw.replace(/\/$/, "").replace("localhost", "127.0.0.1")
}

export function mcpHttpUrl(origin = apiOrigin()): string {
  return `${origin}/mcp`
}

export function mcpHttpConfig(token: string, origin = apiOrigin()) {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  return { url: mcpHttpUrl(origin), headers }
}

function b64json(obj: unknown): string {
  const json = JSON.stringify(obj)
  return btoa(unescape(encodeURIComponent(json)))
}

export function cursorMcpInstallLink(token: string, origin = apiOrigin()): string {
  return `cursor://anysphere.cursor-deeplink/mcp/install?name=chronicum&config=${encodeURIComponent(b64json(mcpHttpConfig(token, origin)))}`
}

export function vscodeMcpInstallLink(token: string, origin = apiOrigin(), scheme = "vscode"): string {
  const cfg = {
    name: "chronicum",
    type: "http",
    url: mcpHttpUrl(origin),
    headers: mcpHttpConfig(token, origin).headers,
  }
  return `${scheme}:mcp/install?${encodeURIComponent(JSON.stringify(cfg))}`
}

export function claudeDesktopSnippet(token: string, origin = apiOrigin()): string {
  return JSON.stringify({ mcpServers: { chronicum: mcpHttpConfig(token, origin) } }, null, 2)
}

export function claudeCodeCommand(token: string, origin = apiOrigin()): string {
  const url = mcpHttpUrl(origin)
  const header = token ? ` --header "Authorization: Bearer ${token}"` : ""
  return `claude mcp add --transport http chronicum ${url}${header}`
}

export function vscodeMcpJsonSnippet(token: string, origin = apiOrigin()): string {
  return JSON.stringify(
    {
      servers: {
        chronicum: {
          type: "http",
          url: mcpHttpUrl(origin),
          headers: mcpHttpConfig(token, origin).headers,
        },
      },
    },
    null,
    2,
  )
}

export function windsurfSnippet(token: string, origin = apiOrigin()): string {
  return claudeDesktopSnippet(token, origin)
}
