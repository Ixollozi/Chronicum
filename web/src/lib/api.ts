export type EntryKind = "knowledge" | "task" | "rule" | string;

export type ComposerMode = "auto" | "ask" | "knowledge" | "task" | "rule";

export type MaterialListItem = {
  id: string;
  title: string | null;
  source_type: string;
  entry_kind?: EntryKind;
  source_url: string | null;
  summary: string | null;
  main_idea: string | null;
  category: string | null;
  tags: string[];
  usefulness_score: number | null;
  processing_status: string;
  processing_stage: string | null;
  trust_level?: "mcp_safe" | "review" | "weak" | string | null;
  analysis_confidence?: number | null;
  created_at: string | null;
};

export type MaterialLink = {
  url: string;
  title?: string;
  source?: string;
};

export type CodeBlock = {
  title?: string;
  language?: string;
  code: string;
};

export type MaterialDetail = MaterialListItem & {
  raw_text: string | null;
  key_ideas: string[];
  practical_takeaways: string[];
  tools: string[];
  technologies: string[];
  links: Array<string | MaterialLink>;
  code_blocks?: CodeBlock[];
  usefulness_reason: string | null;
  error_message: string | null;
  duration_seconds: number | null;
  has_audio?: boolean;
  usage_tokens_in: number;
  usage_tokens_out: number;
  usage_audio_seconds: number;
  digest?: string | null;
  detailed_explanation?: string | null;
  objective_evaluation?: {
    pros?: string[];
    cons?: string[];
    risks?: string[];
    best_for?: string;
    verdict?: string;
    score_agreement?: number;
  } | null;
  ai_opinion?: string | null;
  ai_opinion_sources?: string[];
  ai_opinion_at?: string | null;
  key_moments?: {
    t_sec: number;
    caption: string;
    why: string;
    when_to_apply: string;
    quote?: string;
    visible_text?: string;
    image_relpath?: string | null;
  }[];
};

export function normalizeMaterialLinks(links: Array<string | MaterialLink> | null | undefined): MaterialLink[] {
  if (!links?.length) return [];
  const out: MaterialLink[] = [];
  const seen = new Set<string>();
  for (const item of links) {
    const link: MaterialLink =
      typeof item === "string"
        ? { url: item, title: "", source: "" }
        : { url: item.url || "", title: item.title || "", source: item.source || "" };
    const url = (link.url || "").trim();
    if (!url) continue;
    const key = url.replace(/^https?:\/\//i, "").replace(/^www\./i, "").replace(/\/$/, "").toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ ...link, url });
  }
  return out;
}

export function normalizeCodeBlocks(blocks: CodeBlock[] | null | undefined): CodeBlock[] {
  if (!blocks?.length) return [];
  return blocks
    .map((b) => ({
      title: (b.title || "").trim(),
      language: (b.language || "text").trim() || "text",
      code: (b.code || "").replace(/\r\n/g, "\n").trim(),
    }))
    .filter((b) => b.code.length > 0);
}

export type PersonalProfile = {
  role: string;
  goals: string[];
  focuses: string[];
  anti_focuses: string[];
  horizon: "now" | "90d" | "long" | string;
  current_priorities: string;
};

export type AppSettings = {
  default_depth: "compact" | "standard" | "detailed" | string;
  auto_moments: boolean;
  video_full?: boolean;
  provider_order: string[];
  personal_profile: PersonalProfile;
};

export type ApiKeyItem = {
  id: number | string;
  provider: string;
  label: string | null;
  hint: string;
  enabled: boolean;
  source: string;
  last_ok_at: string | null;
  last_error: string | null;
};

export type ComposerBatchItem = {
  id: string;
  status: string;
  already_exists: boolean;
};

export type ComposerResult = {
  intent: "url" | "note" | "search" | "refuse" | "task" | "rule" | "batch" | string;
  message: string;
  material_id: string | null;
  answer: string | null;
  sources: MaterialListItem[];
  already_exists?: boolean;
  items?: ComposerBatchItem[];
};

export type GraphNodeType = "material" | "tag" | "category" | string;

export type GraphPayload = {
  nodes: {
    id: string;
    type: GraphNodeType;
    title: string;
    category: string | null;
    tags: string[];
    score: number | null;
    status: string;
    source_type: string;
    summary?: string | null;
    degree: number;
  }[];
  links: { source: string; target: string; reason: string; weight?: number }[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8010";
const TOKEN_KEY = "chronicum_token";

export function apiBaseUrl(): string {
  return API_URL.replace(/\/$/, "");
}

export function getToken(): string {
  if (typeof window === "undefined") return "";
  // Session only from localStorage after Google login — never from NEXT_PUBLIC_* env
  // (that would bake the admin token into the browser bundle).
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
}

export function googleLoginUrl(): string {
  return `${apiBaseUrl()}/api/auth/google`;
}

export type AuthMe = {
  id: string | null;
  email: string | null;
  name: string | null;
  picture_url: string | null;
  is_admin: boolean;
  google_oauth_ready: boolean;
};

/** True only for a Google account session (not admin/bootstrap WEB_API_TOKEN). */
export function isGoogleSession(me: AuthMe | null | undefined): boolean {
  return Boolean(me && me.id && !me.is_admin);
}

export type PersonalApiToken = {
  id: string;
  label: string | null;
  token_prefix: string;
  created_at: string | null;
  last_used_at: string | null;
};

export type PersonalApiTokenCreated = PersonalApiToken & { token: string };

/** Thrown when a protected call runs without a session (gate race / logout). */
export class AuthRequiredError extends Error {
  constructor(message = "Нет токена для входа") {
    super(message);
    this.name = "AuthRequiredError";
  }
}

export function isAuthError(err: unknown): boolean {
  if (err instanceof AuthRequiredError) return true;
  const msg = err instanceof Error ? err.message : String(err);
  return /Нет токена для входа|Сессия недействительна|Пользователь не найден|Missing Bearer|Invalid token|User not found/i.test(
    msg
  );
}

function isPublicApiPath(path: string): boolean {
  const p = path.split("?")[0] || path;
  return (
    p === "/health" ||
    p === "/api/auth/status" ||
    p.startsWith("/api/auth/google")
  );
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  const token = getToken();
  if (!token && !isPublicApiPath(path)) {
    throw new AuthRequiredError();
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(init.body instanceof FormData) && !headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    let detail = res.statusText;
    const body = await res.text().catch(() => "");
    if (body) {
      try {
        const data = JSON.parse(body);
        if (typeof data?.detail === "string") detail = data.detail;
        else if (Array.isArray(data?.detail))
          detail = data.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join("; ");
        else detail = JSON.stringify(data);
      } catch {
        detail = body;
      }
    }
    if (detail === "Missing Bearer token") throw new AuthRequiredError();
    if (detail === "Invalid token") throw new AuthRequiredError("Сессия недействительна — войди снова");
    if (detail === "User not found") throw new AuthRequiredError("Пользователь не найден");
    throw new Error(detail || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const client = {
  listMaterials: (params: Record<string, string | number | undefined> = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return api<{ items: MaterialListItem[]; total: number }>(`/api/materials${qs ? `?${qs}` : ""}`);
  },
  materialsBusy: () =>
    api<{ queued: number; processing: number; total: number }>("/api/materials/busy"),
  getMaterial: (id: string) => api<MaterialDetail>(`/api/materials/${id}`),
  deleteMaterial: (id: string) =>
    api<{ deleted: string[]; missing: string[]; count: number }>(`/api/materials/${id}`, {
      method: "DELETE",
    }),
  deleteMaterials: (ids: string[]) =>
    api<{ deleted: string[]; missing: string[]; count: number }>("/api/materials/delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  search: (query: string) =>
    api<{ answer: string; sources: MaterialListItem[] }>("/api/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),
  composer: (text: string, mode: ComposerMode = "auto") =>
    api<ComposerResult>("/api/composer", {
      method: "POST",
      body: JSON.stringify({ text, mode }),
    }),
  graph: () => api<GraphPayload>("/api/graph"),
  createNote: (text: string) =>
    api<{ id: string; status: string; message: string }>("/api/materials/note", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  createUrl: (url: string) =>
    api<{ id: string; status: string; message: string }>("/api/materials/url", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),
  upload: async (file: File, kind?: string, sourceUrl?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (kind) fd.append("kind", kind);
    if (sourceUrl?.trim()) fd.append("source_url", sourceUrl.trim());
    return api<{ id: string; status: string; message: string }>("/api/materials/upload", {
      method: "POST",
      body: fd,
    });
  },
  updateScore: (id: string, score: number, reason?: string) =>
    api<MaterialDetail>(`/api/materials/${id}/score`, {
      method: "PATCH",
      body: JSON.stringify({ score, reason: reason || undefined }),
    }),
  retry: (id: string) => api<{ id: string }>(`/api/materials/${id}/retry`, { method: "POST" }),
  processFull: (id: string) =>
    api<{ id: string }>(`/api/materials/${id}/process-full`, { method: "POST" }),
  categories: () => api<{ id: number; name: string }[]>("/api/categories"),
  createCategory: (name: string) =>
    api<{ id: number; name: string }>("/api/categories", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  getSettings: () => api<AppSettings>("/api/settings"),
  health: () =>
    api<{
      status: string;
      mode: string;
      ai_ready: boolean;
      mcp_http?: boolean;
      mcp_path?: string | null;
    }>("/health"),
  patchSettings: (body: Partial<AppSettings>) =>
    api<AppSettings>("/api/settings", { method: "PATCH", body: JSON.stringify(body) }),
  listKeys: (provider?: string) => {
    const q = provider ? `?provider=${encodeURIComponent(provider)}` : "";
    return api<ApiKeyItem[]>(`/api/settings/keys${q}`);
  },
  addKeys: (provider: string, keys: string, label?: string) =>
    api<{ added: number; message: string }>("/api/settings/keys", {
      method: "POST",
      body: JSON.stringify({ provider, keys, label: label || null }),
    }),
  patchKey: (id: number, body: { enabled?: boolean; label?: string }) =>
    api<ApiKeyItem>(`/api/settings/keys/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteKey: (id: number) => api<{ ok: boolean }>(`/api/settings/keys/${id}`, { method: "DELETE" }),
  checkKeys: (provider: string) =>
    api<{ provider: string; ok: number; failed: number; details: unknown[] }>(
      `/api/settings/keys/check?provider=${encodeURIComponent(provider)}`,
      { method: "POST" }
    ),
  digest: (id: string) => api<MaterialDetail>(`/api/materials/${id}/digest`, { method: "POST" }),
  explain: (id: string) => api<MaterialDetail>(`/api/materials/${id}/explain`, { method: "POST" }),
  evaluate: (id: string) => api<MaterialDetail>(`/api/materials/${id}/evaluate`, { method: "POST" }),
  opinion: (id: string) => api<MaterialDetail>(`/api/materials/${id}/opinion`, { method: "POST" }),
  moments: (id: string) => api<MaterialDetail>(`/api/materials/${id}/moments`, { method: "POST" }),
  momentUrl: (id: string, filename: string) => `${API_URL}/api/materials/${id}/moments/${filename}`,
  listClients: (q?: string) =>
    api<ClientCard[]>(`/api/clients${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  createClient: (body: {
    display_name: string;
    aliases?: string[];
    context_line?: string;
  }) =>
    api<ClientCard>("/api/clients", { method: "POST", body: JSON.stringify(body) }),
  getClient: (id: string) => api<ClientDetail>(`/api/clients/${id}`),
  updateClient: (id: string, body: Record<string, unknown>) =>
    api<ClientCard>(`/api/clients/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  appendClientMessage: (id: string, body: string, refresh_summary = false) =>
    api<ClientDetail>(`/api/clients/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ body, refresh_summary, source: "web" }),
    }),
  pastePreview: (id: string, text: string) =>
    api<{ count: number; speakers: string[]; messages: unknown[] }>(`/api/clients/${id}/paste/preview`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  pasteCommit: (id: string, text: string, speaker_roles: Record<string, string> = {}) =>
    api<ClientDetail & { added: number; skipped_duplicates: number }>(`/api/clients/${id}/paste`, {
      method: "POST",
      body: JSON.stringify({ text, speaker_roles, source: "web" }),
    }),
  refreshClientSummary: (id: string) =>
    api<ClientCard>(`/api/clients/${id}/summary/refresh`, { method: "POST" }),
  patchMaterial: (id: string, body: Record<string, unknown>) =>
    api<MaterialDetail>(`/api/materials/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  getClientContext: (q: string) =>
    api<ClientContextPack>(`/api/clients/resolve/context?q=${encodeURIComponent(q)}`),
  searchFeedback: (query: string, useful: boolean) =>
    api<{ ok: boolean }>("/api/search/feedback", {
      method: "POST",
      body: JSON.stringify({ query, useful }),
    }),
  authMe: () => api<AuthMe>("/api/auth/me"),
  authStatus: () =>
    api<{ google_oauth_ready: boolean; web_origin: string }>("/api/auth/status"),
  authLogout: () => api<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  listAuthTokens: () => api<PersonalApiToken[]>("/api/auth/tokens"),
  createAuthToken: (label?: string) =>
    api<PersonalApiTokenCreated>("/api/auth/tokens", {
      method: "POST",
      body: JSON.stringify({ label: label || null }),
    }),
  revokeAuthToken: (id: string) =>
    api<{ ok: boolean }>(`/api/auth/tokens/${id}`, { method: "DELETE" }),
};

export type ClientCard = {
  id: string;
  display_name: string;
  aliases: string[];
  contacts: Record<string, unknown>;
  context_line: string | null;
  canon_facts?: string | null;
  summary: string | null;
  summary_version: number;
  summary_updated_at: string | null;
  summary_stale?: boolean;
  messages_since_summary?: number;
  message_count: number;
};

export type ClientDetail = ClientCard & {
  messages: {
    id: string;
    body: string;
    source: string;
    speaker?: string;
    speaker_name?: string | null;
    kind?: string;
    event_at?: string | null;
    created_at: string | null;
  }[];
};

export type ClientContextPack = {
  client: Record<string, unknown>;
  recent_messages: { id: string; body: string; source?: string; created_at?: string }[];
  search_hits: { id: string; body: string }[];
};
