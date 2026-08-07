export type MaterialListItem = {
  id: string;
  title: string | null;
  source_type: string;
  source_url: string | null;
  summary: string | null;
  main_idea: string | null;
  category: string | null;
  tags: string[];
  usefulness_score: number | null;
  processing_status: string;
  processing_stage: string | null;
  created_at: string | null;
};

export type MaterialDetail = MaterialListItem & {
  raw_text: string | null;
  key_ideas: string[];
  practical_takeaways: string[];
  tools: string[];
  technologies: string[];
  links: string[];
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

export type ComposerResult = {
  intent: "url" | "note" | "search" | "refuse" | string;
  message: string;
  material_id: string | null;
  answer: string | null;
  sources: MaterialListItem[];
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

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_KEY = "chronicum_token";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(TOKEN_KEY) || process.env.NEXT_PUBLIC_WEB_API_TOKEN || "";
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(init.body instanceof FormData) && !headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
      else if (Array.isArray(data?.detail))
        detail = data.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join("; ");
      else detail = JSON.stringify(data);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail || res.statusText);
  }
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
  composer: (text: string) =>
    api<ComposerResult>("/api/composer", {
      method: "POST",
      body: JSON.stringify({ text }),
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
  upload: async (file: File, kind?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (kind) fd.append("kind", kind);
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
};
