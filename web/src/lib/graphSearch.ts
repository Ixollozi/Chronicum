import type { GraphPayload } from "@/lib/api";

export type GraphSearchHit = GraphPayload["nodes"][number];

export function nodeTypeLabel(type: string): string {
  if (type === "category") return "категория";
  if (type === "tag") return "тег";
  return "материал";
}

/** Ranked search across materials, categories and tags. */
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

    let s = 0;
    if (title === q) s = 100;
    else if (title.startsWith(q)) s = 80;
    else if (title.includes(q)) s = 60;
    else if (tags.some((t) => t === q || t.startsWith(q) || t.includes(q))) s = 50;
    else if (cat.includes(q)) s = 40;
    else if (summary.includes(q)) s = 30;
    else continue;

    if (n.type === "category") s += 8;
    else if (n.type === "tag") s += 4;
    else s += Math.min(6, (n.score ?? 0) / 2);

    scored.push({ n, s });
  }

  scored.sort((a, b) => b.s - a.s || a.n.title.localeCompare(b.n.title, "ru"));
  return scored.slice(0, limit).map((x) => x.n);
}
