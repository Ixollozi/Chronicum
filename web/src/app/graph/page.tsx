"use client";

import { forceCollide } from "d3-force";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraphCanvas from "@/components/ForceGraphCanvas";
import { client, type GraphPayload } from "@/lib/api";

type GNode = GraphPayload["nodes"][number] & {
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
  val?: number;
};
type GLink = { source: string | GNode; target: string | GNode; reason: string; weight?: number };

function linkEnds(l: GLink): [string, string] {
  const s = typeof l.source === "object" ? l.source.id : l.source;
  const t = typeof l.target === "object" ? l.target.id : l.target;
  return [s, t];
}

function isTopic(n: { type: string }) {
  return n.type === "tag" || n.type === "category";
}

function nodeRadius(n: GNode): number {
  if (n.type === "category") return 8 + Math.min(10, n.degree * 0.7);
  if (n.type === "tag") return 5 + Math.min(8, n.degree * 0.55);
  return 2.4 + Math.min(3.2, n.degree * 0.22);
}

function ruLinks(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} связь`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} связи`;
  return `${n} связей`;
}

function clampTooltip(x: number, y: number, w: number, h: number) {
  const pad = 12;
  const tw = 240;
  const th = 72;
  let left = x + 16;
  let top = y + 16;
  if (left + tw > w - pad) left = x - tw - 12;
  if (top + th > h - pad) top = y - th - 12;
  if (left < pad) left = pad;
  if (top < pad) top = pad;
  return { left, top };
}

/**
 * Obsidian-style graph:
 * - all nodes visible at once
 * - force simulation always runs (nodes move)
 * - click = select + zoom to node
 * - double-click material = open page
 * - Esc / «Ко всем» = zoom to fit whole graph
 */
export default function GraphPage() {
  const router = useRouter();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null);
  const fittedRef = useRef(false);
  const lastClickRef = useRef<{ id: string; t: number } | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const posRef = useRef(new Map<string, { x: number; y: number; vx?: number; vy?: number }>());

  const [raw, setRaw] = useState<GraphPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 640 });
  const [hover, setHover] = useState<GNode | null>(null);
  const [q, setQ] = useState("");
  const [sheet, setSheet] = useState<GNode | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const [forcesReady, setForcesReady] = useState(0);

  const load = useCallback(async () => {
    try {
      const g = await client.graph();
      setRaw(g);
      setError(null);
      fittedRef.current = false;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка графа");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    function measure() {
      setSize({ w: window.innerWidth, h: window.innerHeight });
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphPayload["nodes"][number]>();
    if (!raw) return m;
    for (const n of raw.nodes) m.set(n.id, n);
    return m;
  }, [raw]);

  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>();
    if (!raw) return map;
    for (const l of raw.links) {
      if (!map.has(l.source)) map.set(l.source, new Set());
      if (!map.has(l.target)) map.set(l.target, new Set());
      map.get(l.source)!.add(l.target);
      map.get(l.target)!.add(l.source);
    }
    return map;
  }, [raw]);

  const categories = useMemo(
    () =>
      raw
        ? Array.from(new Set(raw.nodes.filter((n) => n.type === "category").map((n) => n.title))).sort()
        : [],
    [raw]
  );
  const tags = useMemo(
    () =>
      raw ? Array.from(new Set(raw.nodes.filter((n) => n.type === "tag").map((n) => n.title))).sort() : [],
    [raw]
  );

  const filtered = useMemo(() => {
    if (!raw) return { nodes: [] as GNode[], links: [] as GLink[] };
    const qLower = q.trim().toLowerCase();

    const pass = (n: GraphPayload["nodes"][number]) => {
      if (n.type === "material") {
        if (minScore > 0 && (n.score ?? 0) < minScore) return false;
        if (category && (n.category || "").toLowerCase() !== category.toLowerCase()) return false;
        if (tag && !(n.tags || []).some((t) => t.toLowerCase() === tag.toLowerCase())) return false;
        if (
          qLower &&
          !n.title.toLowerCase().includes(qLower) &&
          !(n.summary || "").toLowerCase().includes(qLower)
        ) {
          return false;
        }
        return true;
      }
      if (!isTopic(n)) return false;
      if (category && n.type === "category" && n.title.toLowerCase() !== category.toLowerCase()) return false;
      if (tag && n.type === "tag" && n.title.toLowerCase() !== tag.toLowerCase()) return false;
      if (qLower && !n.title.toLowerCase().includes(qLower)) return false;
      return true;
    };

    const keep = new Set<string>();
    for (const n of raw.nodes) {
      if (pass(n)) keep.add(n.id);
    }
    // Always keep topic neighbors of kept materials so spokes remain
    for (const id of [...keep]) {
      const n = nodeById.get(id);
      if (!n || n.type !== "material") continue;
      for (const nb of adjacency.get(id) || []) {
        const tn = nodeById.get(nb);
        if (tn && isTopic(tn)) keep.add(nb);
      }
    }

    if (category) keep.add(`cat:${category.toLowerCase()}`);
    if (tag) keep.add(`tag:${tag.toLowerCase()}`);

    const nodes: GNode[] = raw.nodes
      .filter((n) => keep.has(n.id))
      .map((n) => {
        const prev = posRef.current.get(n.id);
        const val = isTopic(n) ? 8 + Math.min(16, n.degree) : 2 + Math.min(6, n.degree);
        if (!prev) return { ...n, val };
        return {
          ...n,
          val,
          x: prev.x,
          y: prev.y,
          vx: prev.vx ?? 0,
          vy: prev.vy ?? 0,
        };
      });

    const ids = new Set(nodes.map((n) => n.id));
    const links: GLink[] = raw.links.filter((l) => {
      if (!ids.has(l.source) || !ids.has(l.target)) return false;
      const a = nodeById.get(l.source);
      const b = nodeById.get(l.target);
      if (!a || !b) return false;
      // materials ↔ topics
      if (a.type === "material" || b.type === "material") return true;
      // topic constellation: belongs + cooccur only (no dense clique noise)
      if (isTopic(a) && isTopic(b)) return l.reason === "belongs" || l.reason === "cooccur";
      return false;
    });

    return { nodes, links };
  }, [raw, q, category, tag, minScore, adjacency, nodeById]);

  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || filtered.nodes.length === 0) return;

    const charge = fg.d3Force("charge");
    if (charge?.strength) {
      charge.strength((node: GNode) => {
        // Weaker hub repulsion → clusters sit closer instead of flying to corners
        if (node.type === "category") return -160;
        if (node.type === "tag") return -90;
        return -40;
      });
      if (charge.distanceMax) charge.distanceMax(260);
    }

    const link = fg.d3Force("link");
    if (link?.distance) {
      link.distance((l: GLink) => {
        const [a, b] = linkEnds(l);
        const na = nodeById.get(a);
        const nb = nodeById.get(b);
        if (na && nb && na.type === "category" && nb.type === "tag") return 48;
        if (na && nb && na.type === "tag" && nb.type === "category") return 48;
        if (na && nb && isTopic(na) && isTopic(nb)) return 42;
        return 28;
      });
      if (link.strength) {
        link.strength((l: GLink) => (l.reason === "belongs" ? 0.95 : 0.55));
      }
    }

    fg.d3Force(
      "collide",
      forceCollide((n: GNode) => nodeRadius(n) + (isTopic(n) ? 6 : 3)).strength(0.9)
    );

    // Pull the whole map toward the center so main hubs don't drift into voids
    const center = fg.d3Force("center");
    if (center?.strength) center.strength(0.08);

    // Keep the graph alive — reheating is what makes nodes move after data/filter changes
    fg.d3ReheatSimulation?.();
  }, [filtered.nodes.length, filtered.links.length, forcesReady, nodeById]);

  const hoverSet = useMemo(() => {
    if (!hover) return null;
    const set = new Set<string>([hover.id]);
    for (const id of adjacency.get(hover.id) || []) {
      if (filtered.nodes.some((n) => n.id === id)) set.add(id);
    }
    return set;
  }, [hover, adjacency, filtered.nodes]);

  const neighborsOfSheet = useMemo(() => {
    if (!sheet || !raw) return [];
    const ids = adjacency.get(sheet.id) || new Set();
    const seen = new Set<string>();
    const out: GraphPayload["nodes"][number][] = [];
    for (const n of raw.nodes) {
      if (!ids.has(n.id) || n.type !== "material") continue;
      const key = n.title.trim().toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(n);
    }
    return out;
  }, [sheet, adjacency, raw]);

  function zoomToFit() {
    fittedRef.current = true;
    fgRef.current?.zoomToFit?.(700, 90);
  }

  function cameraTo(node: GNode, zoom: number, ms = 550) {
    if (!fgRef.current || node.x == null || node.y == null) return;
    fgRef.current.centerAt(node.x, node.y, ms);
    fgRef.current.zoom(zoom, ms);
  }

  function selectNode(n: GNode) {
    const now = Date.now();
    const prev = lastClickRef.current;
    lastClickRef.current = { id: n.id, t: now };
    if (prev && prev.id === n.id && now - prev.t < 320 && n.type === "material") {
      router.push(`/m/${n.id}`);
      return;
    }
    setSheet(n);
    cameraTo(n, isTopic(n) ? 1.85 : 2.35, 550);
  }

  function clearSelection() {
    setSheet(null);
    zoomToFit();
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") clearSelection();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const empty = raw && filtered.nodes.length === 0;
  const activeFilters = Boolean(q || category || tag || minScore);

  return (
    <section className="fixed inset-0 z-0 bg-[#000000]">
      <div
        ref={stageRef}
        className="absolute inset-0"
        onMouseMove={(e) => {
          const rect = stageRef.current?.getBoundingClientRect();
          if (!rect) return;
          setPointer({ x: e.clientX - rect.left, y: e.clientY - rect.top });
        }}
      >
        {error && <p className="absolute left-4 top-20 z-30 text-sm text-[var(--warn)]">{error}</p>}

        {empty ? (
          <div className="flex h-full flex-col items-center justify-center px-6 text-center">
            <p style={{ fontFamily: "var(--font-display)" }} className="text-3xl">
              Сеть пуста
            </p>
            <Link href="/" className="mt-6 text-[var(--accent)] hover:underline">
              На главную →
            </Link>
          </div>
        ) : (
          <ForceGraphCanvas
            ref={fgRef}
            width={size.w}
            height={size.h}
            graphData={filtered}
            backgroundColor="#000000"
            nodeRelSize={3}
            warmupTicks={60}
            cooldownTicks={180}
            d3AlphaDecay={0.022}
            d3VelocityDecay={0.28}
            enableNodeDrag
            onEngineStop={() => {
              if (!fittedRef.current && filtered.nodes.length > 0) {
                fittedRef.current = true;
                fgRef.current?.zoomToFit(750, 90);
              }
            }}
            onRenderFramePost={() => {
              if (forcesReady === 0 && fgRef.current) setForcesReady(1);
              for (const n of filtered.nodes) {
                if (n.x == null || n.y == null) continue;
                posRef.current.set(n.id, { x: n.x, y: n.y, vx: n.vx, vy: n.vy });
              }
            }}
            linkColor={(l: GLink) => {
              const [a, b] = linkEnds(l);
              const lit = !hoverSet || (hoverSet.has(a) && hoverSet.has(b));
              if (hoverSet && !lit) return "rgba(255,255,255,0.03)";
              if (hoverSet && lit) return "rgba(255,255,255,0.55)";
              if (l.reason === "belongs") return "rgba(255,255,255,0.2)";
              return "rgba(255,255,255,0.1)";
            }}
            linkWidth={(l: GLink) => {
              const [a, b] = linkEnds(l);
              if (hoverSet && hoverSet.has(a) && hoverSet.has(b)) return 1.2;
              return l.reason === "belongs" ? 0.8 : 0.4;
            }}
            onNodeHover={(n: GNode | null) => setHover(n || null)}
            onNodeClick={(n: GNode) => selectNode(n)}
            onBackgroundClick={() => setSheet(null)}
            nodeCanvasObject={(node: GNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
              const n = node;
              const r = nodeRadius(n);
              const hot = hover?.id === n.id || sheet?.id === n.id;
              const topic = isTopic(n);
              const dimmed = Boolean(hoverSet && !hoverSet.has(n.id));

              ctx.globalAlpha = dimmed ? 0.12 : 1;

              if (topic && hot && !dimmed) {
                const grd = ctx.createRadialGradient(n.x || 0, n.y || 0, 0, n.x || 0, n.y || 0, r * 2.8);
                grd.addColorStop(0, "rgba(255,255,255,0.2)");
                grd.addColorStop(1, "rgba(255,255,255,0)");
                ctx.beginPath();
                ctx.arc(n.x || 0, n.y || 0, r * 2.8, 0, 2 * Math.PI);
                ctx.fillStyle = grd;
                ctx.fill();
              }

              ctx.beginPath();
              ctx.arc(n.x || 0, n.y || 0, r, 0, 2 * Math.PI);
              if (n.type === "category") ctx.fillStyle = hot ? "#ffffff" : "#f2f2f2";
              else if (n.type === "tag") ctx.fillStyle = hot ? "#ffffff" : n.degree >= 3 ? "#d0d0d0" : "#7a7a7a";
              else ctx.fillStyle = hot ? "#e8e8e8" : "#555555";
              ctx.fill();

              const showLabel =
                hot ||
                (topic && (n.degree >= 2 || n.type === "category")) ||
                (!topic && globalScale > 1.6);
              if (showLabel && n.title && !dimmed) {
                const fontSize = (topic ? 13 : 10.5) / globalScale;
                ctx.font = `${topic && (n.type === "category" || n.degree >= 3) ? 600 : 400} ${fontSize}px Figtree, sans-serif`;
                ctx.textAlign = "center";
                ctx.textBaseline = "top";
                const short = n.title.length > 28 ? `${n.title.slice(0, 27)}…` : n.title;
                const fade = topic && !hot && globalScale < 0.8 ? 0.55 : 0.92;
                ctx.fillStyle = "rgba(0,0,0,0.65)";
                ctx.fillText(short, (n.x || 0) + 0.6 / globalScale, (n.y || 0) + r + 2.5 + 0.6 / globalScale);
                ctx.fillStyle = `rgba(242,242,242,${fade})`;
                ctx.fillText(short, n.x || 0, (n.y || 0) + r + 2.5);
              }
              ctx.globalAlpha = 1;
            }}
            nodePointerAreaPaint={(node: GNode, color: string, ctx: CanvasRenderingContext2D) => {
              const r = nodeRadius(node) + 7;
              ctx.beginPath();
              ctx.arc(node.x || 0, node.y || 0, r, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.fill();
            }}
          />
        )}

        {hover && !sheet && (
          <div
            className="pointer-events-none absolute z-30 w-[min(220px,70vw)] rounded-md border border-white/10 bg-black/85 px-3 py-2 shadow-xl"
            style={clampTooltip(pointer.x, pointer.y, size.w, size.h)}
          >
            <p className="text-[13px] font-medium leading-snug text-white">{hover.title}</p>
            <p className="mt-1 text-[11px] text-white/50">
              {hover.type === "material"
                ? `${hover.source_type} · ${hover.score ?? "—"}`
                : `${hover.type === "category" ? "категория" : "тег"} · ${ruLinks(hover.degree)}`}
            </p>
          </div>
        )}
      </div>

      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 p-3 md:p-4 md:pr-52">
        <div className="pointer-events-auto flex max-w-md flex-col gap-2">
          <div className="flex items-center gap-2 rounded-full border border-white/10 bg-black/55 px-3 py-1.5 backdrop-blur-md">
            <input
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                fittedRef.current = false;
              }}
              placeholder="Поиск…"
              className="min-w-0 flex-1 bg-transparent py-1.5 text-sm text-white outline-none placeholder:text-white/35"
            />
            <button
              type="button"
              onClick={() => setFiltersOpen((v) => !v)}
              className={`text-xs ${filtersOpen || activeFilters ? "text-[var(--accent)]" : "text-white/45"}`}
            >
              ···
            </button>
          </div>
          {filtersOpen && (
            <div className="flex flex-wrap gap-2 rounded-xl border border-white/10 bg-black/70 p-3 backdrop-blur-md">
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="rounded-md border border-white/10 bg-transparent px-2 py-1.5 text-sm text-white"
              >
                <option value="">Категория</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <select
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                className="rounded-md border border-white/10 bg-transparent px-2 py-1.5 text-sm text-white"
              >
                <option value="">Тег</option>
                {tags.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <select
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                className="rounded-md border border-white/10 bg-transparent px-2 py-1.5 text-sm text-white"
              >
                <option value={0}>Score</option>
                <option value={5}>≥ 5</option>
                <option value={7}>≥ 7</option>
                <option value={8}>≥ 8</option>
              </select>
            </div>
          )}
        </div>
      </div>

      {sheet && (
        <button
          type="button"
          onClick={() => clearSelection()}
          className={`absolute left-4 z-40 rounded-full bg-white px-5 py-2.5 text-sm font-medium text-black shadow-lg transition hover:bg-white/90 active:scale-[0.98] md:left-6 md:px-6 md:py-3 md:text-base ${
            sheet ? "bottom-[calc(46dvh+0.75rem)] md:bottom-8" : "bottom-24 md:bottom-8"
          }`}
        >
          ← Ко всем
        </button>
      )}

      {sheet && (
        <aside className="absolute bottom-20 left-0 right-0 z-30 mx-auto max-h-[46dvh] w-full overflow-y-auto border-t border-white/10 bg-black/90 px-4 py-4 backdrop-blur-md md:bottom-6 md:left-auto md:right-4 md:max-h-[70dvh] md:w-[320px] md:rounded-xl md:border">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <p className="text-[10px] uppercase tracking-[0.2em] text-white/40">
                {sheet.type === "material" ? sheet.source_type : sheet.type === "category" ? "категория" : "тег"}
              </p>
              <h2 className="mt-1 text-xl text-white" style={{ fontFamily: "var(--font-display)" }}>
                {sheet.title}
              </h2>
            </div>
            <button type="button" className="text-white/50" onClick={() => setSheet(null)}>
              ✕
            </button>
          </div>

          {sheet.type === "material" ? (
            <>
              <p className="text-sm text-white/50">
                {sheet.category || "без категории"} · score {sheet.score ?? "—"}
              </p>
              {sheet.summary && <p className="mt-3 text-sm text-white/80 line-clamp-5">{sheet.summary}</p>}
              <button
                type="button"
                className="mt-5 w-full border border-[var(--accent)] bg-[var(--accent)] px-4 py-2.5 text-sm text-[var(--btn-fg)]"
                onClick={() => router.push(`/m/${sheet.id}`)}
              >
                Открыть материал
              </button>
            </>
          ) : (
            <>
              <p className="text-sm text-white/50">{neighborsOfSheet.length} материалов</p>
              <ul className="mt-3 space-y-2">
                {neighborsOfSheet.slice(0, 14).map((n) => (
                  <li key={n.id}>
                    <button
                      type="button"
                      className="w-full border-b border-white/10 py-2 text-left text-sm text-white/85 hover:text-[var(--accent)]"
                      onClick={() => {
                        const node = filtered.nodes.find((x) => x.id === n.id);
                        if (node) selectNode(node);
                        else setSheet(n as GNode);
                      }}
                    >
                      {n.title}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
      )}
    </section>
  );
}
