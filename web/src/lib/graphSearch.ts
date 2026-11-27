import type { GraphCluster, GraphPayload } from "@/lib/api";

export type GraphSearchHit = GraphPayload["nodes"][number];

export function nodeTypeLabel(type: string): string {
  if (type === "client") return "клиент";
  if (type === "cluster") return "кластер";
  if (type === "category") return "категория";
  if (type === "tag") return "тег";
  return "материал";
}

/** Ranked search across materials, clients, tags, tools, and categories. */
export function searchGraphNodes(
  nodes: GraphSearchHit[],
  query: string,
  limit = 8
): GraphSearchHit[] {
  const q = query.trim().toLowerCase();
  if (!q || q.length < 1) return [];

  const scored: { n: GraphSearchHit; s: number }[] = [];
  for (const n of nodes) {
    const title = (n.title || "").toLowerCase();
    const summary = (n.summary || "").toLowerCase();
    const cat = (n.category || "").toLowerCase();
    const tags = (n.tags || []).map((t) => t.toLowerCase());
    const tools = (n.tools || []).map((t) => t.toLowerCase());

    let s = 0;
    if (title === q) s = 100;
    else if (title.startsWith(q)) s = 82;
    else if (title.includes(q)) s = 64;
    else if (tools.some((t) => t === q || t.startsWith(q) || t.includes(q))) s = 56;
    else if (tags.some((t) => t === q || t.startsWith(q) || t.includes(q))) s = 48;
    else if (cat.includes(q)) s = 40;
    else if (summary.includes(q)) s = 28;
    else continue;

    if (n.type === "client") s += 10;
    else if (n.type === "category") s += 8;
    else if (n.type === "tag") s += 4;
    else s += Math.min(6, (n.score ?? 0) / 2);

    scored.push({ n, s });
  }

  scored.sort((a, b) => b.s - a.s || a.n.title.localeCompare(b.n.title, "ru"));
  return scored.slice(0, limit).map((x) => x.n);
}

/** Ranked search across clusters and subclusters. */
export function searchGraphClusters(
  clusters: GraphCluster[],
  query: string,
  limit = 4
): GraphCluster[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];

  const scored: { c: GraphCluster; s: number }[] = [];
  for (const c of clusters) {
    const label = (c.label || "").toLowerCase();
    const cat = (c.category || "").toLowerCase();
    const tools = (c.top_tools || []).map((t) => t.toLowerCase());
    const tags = (c.top_tags || []).map((t) => t.toLowerCase());

    let s = 0;
    if (label === q) s = 100;
    else if (label.startsWith(q)) s = 84;
    else if (label.includes(q)) s = 68;
    else if (cat === q || cat.startsWith(q)) s = 60;
    else if (tools.some((t) => t.includes(q))) s = 48;
    else if (tags.some((t) => t.includes(q))) s = 40;
    else continue;

    scored.push({ c, s: s + Math.min(8, c.count / 5) });
  }

  scored.sort((a, b) => b.s - a.s || b.c.count - a.c.count);
  return scored.slice(0, limit).map((x) => x.c);
}
