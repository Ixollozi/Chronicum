"use client";

import { forceCollide } from "d3-force";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraphCanvas from "@/components/ForceGraphCanvas";
import { FancySelect } from "@/components/FancySelect";
import { client, type GraphPayload } from "@/lib/api";
import { nodeTypeLabel, searchGraphNodes } from "@/lib/graphSearch";

type GNode = GraphPayload["nodes"][number] & {
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
  val?: number;
};
type GLink = {
  source: string | GNode;
  target: string | GNode;
  reason: string;
  weight?: number;
  label?: string | null;
  similarity?: number | null;
};

function isMaterialLink(reason: string): boolean {
  return reason === "semantic" || reason === "shared_tool";
}

function linkReasonLabel(link: Pick<GLink, "reason" | "label" | "similarity">): string {
  if (link.reason === "shared_tool") {
    return link.label ? `Инструменты: ${link.label}` : "Общие инструменты";
  }
  if (link.reason === "semantic") {
    const pct = link.similarity != null ? ` ${Math.round(link.similarity * 100)}%` : "";
    return `По смыслу${pct}`;
  }
  return link.reason;
}

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
  const forcesInited = useRef(false);
  const prevFilterKey = useRef("");
  const [layoutReady, setLayoutReady] = useState(false);

  const [raw, setRaw] = useState<GraphPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 640 });
  const [hover, setHover] = useState<GNode | null>(null);
  const [q, setQ] = useState("");
  const [sheet, setSheet] = useState<GNode | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchWrapRef = useRef<HTMLDivElement>(null);
  const focusDoneRef = useRef<string | null>(null);
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [showSemanticLinks, setShowSemanticLinks] = useState(true);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const [forcesReady, setForcesReady] = useState(0);
  const [coarsePointer, setCoarsePointer] = useState(false);

  const load = useCallback(async () => {
    try {
      const g = await client.graph();
      // Clone so force-graph cannot mutate payload links/nodes in place
      setRaw({
        nodes: g.nodes.map((n) => ({ ...n })),
        links: g.links.map((l) => ({
          source: typeof l.source === "string" ? l.source : String(l.source),
          target: typeof l.target === "string" ? l.target : String(l.target),
          reason: l.reason,
          weight: l.weight,
          label: l.label ?? null,
          similarity: l.similarity ?? null,
        })),
      });
      setError(null);
      fittedRef.current = false;
      forcesInited.current = false;
      setLayoutReady(false);
      posRef.current.clear();
      prevFilterKey.current = "";
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

  useEffect(() => {
    const mq = window.matchMedia("(pointer: coarse)");
    const sync = () => setCoarsePointer(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
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
      const [s, t] = linkEnds(l as GLink);
      if (!map.has(s)) map.set(s, new Set());
      if (!map.has(t)) map.set(t, new Set());
      map.get(s)!.add(t);
      map.get(t)!.add(s);
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

  // Stable topology — filters never add/remove nodes from the simulation
  const graphData = useMemo(() => {
    if (!raw) return { nodes: [] as GNode[], links: [] as GLink[] };

    const nodes: GNode[] = raw.nodes.map((n) => {
      const val = isTopic(n) ? 8 + Math.min(16, n.degree) : 2 + Math.min(6, n.degree);
      return { ...n, val };
    });

    const ids = new Set(nodes.map((n) => n.id));
    const links: GLink[] = [];
    for (const l of raw.links) {
      const [s, t] = linkEnds(l as GLink);
      if (!ids.has(s) || !ids.has(t)) continue;
      const a = nodeById.get(s);
      const b = nodeById.get(t);
      if (!a || !b) continue;
      if (a.type === "material" || b.type === "material") {
        links.push({
          source: s,
          target: t,
          reason: l.reason,
          weight: l.weight,
          label: l.label ?? null,
          similarity: l.similarity ?? null,
        });
        continue;
      }
      if (isTopic(a) && isTopic(b) && (l.reason === "belongs" || l.reason === "cooccur")) {
        links.push({ source: s, target: t, reason: l.reason, weight: l.weight });
      }
    }

    return { nodes, links };
  }, [raw, nodeById]);

  // Visibility only — layout coords stay put when filters change
  const visibleIds = useMemo(() => {
    if (!raw) return new Set<string>();
    const qLower = q.trim().toLowerCase();
    const filtering = Boolean(qLower || category || tag || minScore);
    if (!filtering) return new Set(raw.nodes.map((n) => n.id));

    const keep = new Set<string>();
    for (const n of raw.nodes) {
      if (n.type !== "material") continue;
      if (minScore > 0 && (n.score ?? 0) < minScore) continue;
      if (category && (n.category || "").toLowerCase() !== category.toLowerCase()) continue;
      if (tag && !(n.tags || []).some((t) => t.toLowerCase() === tag.toLowerCase())) continue;
      if (
        qLower &&
        !n.title.toLowerCase().includes(qLower) &&
        !(n.summary || "").toLowerCase().includes(qLower)
      ) {
        continue;
      }
      keep.add(n.id);
    }
    for (const id of [...keep]) {
      for (const nb of adjacency.get(id) || []) {
        const tn = nodeById.get(nb);
        if (tn && isTopic(tn)) keep.add(nb);
      }
    }
    if (qLower) {
      for (const n of raw.nodes) {
        if (isTopic(n) && n.title.toLowerCase().includes(qLower)) keep.add(n.id);
      }
    }
    if (category) keep.add(`cat:${category.toLowerCase()}`);
    if (tag) keep.add(`tag:${tag.toLowerCase()}`);
    return keep;
  }, [raw, q, category, tag, minScore, adjacency, nodeById]);

  const isFiltered = visibleIds.size < graphData.nodes.length;
  const filterKey = `${q}|${category}|${tag}|${minScore}`;

  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || graphData.nodes.length === 0) return;

    const charge = fg.d3Force("charge");
    if (charge?.strength) {
      charge.strength((node: GNode) => {
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

    const center = fg.d3Force("center");
    if (center?.strength) center.strength(0.08);

    if (!forcesInited.current) {
      forcesInited.current = true;
      fg.d3ReheatSimulation?.();
    }
  }, [graphData.nodes.length, graphData.links.length, forcesReady, nodeById]);

  function zoomToVisible(ms = 420) {
    const fg = fgRef.current;
    if (!fg) return;
    const pts = graphData.nodes.filter((n) => visibleIds.has(n.id) && n.x != null && n.y != null);
    if (pts.length === 0) return;
    if (!isFiltered) {
      fg.zoomToFit?.(ms, 100);
      return;
    }
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const n of pts) {
      minX = Math.min(minX, n.x!);
      maxX = Math.max(maxX, n.x!);
      minY = Math.min(minY, n.y!);
      maxY = Math.max(maxY, n.y!);
    }
    const pad = 80;
    const w = Math.max(40, maxX - minX + pad * 2);
    const h = Math.max(40, maxY - minY + pad * 2);
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const zoom = Math.min(size.w / w, size.h / h, 3.2);
    fg.centerAt(cx, cy, ms);
    fg.zoom(Math.max(0.45, zoom), ms);
  }

  // Soft camera frame when filters change — never reheat
  useEffect(() => {
    if (prevFilterKey.current === filterKey) return;
    prevFilterKey.current = filterKey;
    if (!layoutReady || graphData.nodes.length === 0) return;
    const t = window.setTimeout(() => zoomToVisible(420), 40);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on filter identity
  }, [filterKey, layoutReady]);

  const hoverSet = useMemo(() => {
    if (!hover) return null;
    const set = new Set<string>([hover.id]);
    for (const id of adjacency.get(hover.id) || []) {
      if (visibleIds.has(id)) set.add(id);
    }
    return set;
  }, [hover, adjacency, visibleIds]);

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

  const relatedMaterialsOfSheet = useMemo(() => {
    if (!sheet || !raw || sheet.type !== "material") return [];
    const out: { node: GraphPayload["nodes"][number]; reason: string; badge: string }[] = [];
    const seen = new Set<string>();
    for (const l of raw.links) {
      if (!isMaterialLink(l.reason)) continue;
      const [s, t] = linkEnds(l as GLink);
      const otherId = s === sheet.id ? t : t === sheet.id ? s : null;
      if (!otherId) continue;
      const key = `${otherId}:${l.reason}`;
      if (seen.has(key)) continue;
      const other = nodeById.get(otherId);
      if (!other || other.type !== "material") continue;
      seen.add(key);
      out.push({ node: other, reason: l.reason, badge: linkReasonLabel(l) });
    }
    return out.slice(0, 8);
  }, [sheet, raw, nodeById]);

  function zoomToFit() {
    fittedRef.current = true;
    zoomToVisible(700);
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

  function focusSearch(query = "") {
    setFiltersOpen(false);
    if (query) setQ(query);
    setSearchOpen(true);
    window.requestAnimationFrame(() => {
      const el = searchInputRef.current;
      if (!el) return;
      el.focus();
      if (query) {
        const len = el.value.length;
        el.setSelectionRange(len, len);
      }
    });
  }

  const searchHits = useMemo(
    () => (searchOpen && q.trim() ? searchGraphNodes(graphData.nodes, q, 8) : []),
    [searchOpen, q, graphData.nodes]
  );

  // Deep-link: /graph?focus=<nodeId>
  useEffect(() => {
    if (!layoutReady || !raw) return;
    const id = new URLSearchParams(window.location.search).get("focus");
    if (!id || focusDoneRef.current === id) return;
    const node = graphData.nodes.find((n) => n.id === id);
    if (!node) return;
    focusDoneRef.current = id;
    // Wait a tick so coords exist after layout
    const t = window.setTimeout(() => selectNode(node), 80);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run when layout/raw ready for a given focus id
  }, [layoutReady, raw, graphData.nodes]);

  useEffect(() => {
    if (!searchOpen) return;
    function onPointer(e: MouseEvent) {
      const t = e.target as Node;
      if (searchWrapRef.current?.contains(t)) return;
      setSearchOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [searchOpen]);

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

  const empty = raw && graphData.nodes.length === 0;
  const noMatches = Boolean(raw && graphData.nodes.length > 0 && visibleIds.size === 0);
  const activeFilters = Boolean(q || category || tag || minScore);

  return (
    <section className="fixed inset-0 z-0 bg-[#000000]">
      <div
        ref={stageRef}
        className="graph-stage absolute inset-0"
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
            graphData={graphData}
            backgroundColor="#000000"
            nodeRelSize={3}
            warmupTicks={0}
            cooldownTicks={220}
            d3AlphaDecay={0.022}
            d3VelocityDecay={0.28}
            enableNodeDrag
            enablePointerInteraction
            onEngineStop={() => {
              if (!layoutReady) setLayoutReady(true);
              if (!fittedRef.current && graphData.nodes.length > 0) {
                fittedRef.current = true;
                fgRef.current?.zoomToFit(750, 90);
              }
            }}
            onRenderFramePost={() => {
              if (forcesReady === 0 && fgRef.current) setForcesReady(1);
              for (const n of graphData.nodes) {
                if (n.x == null || n.y == null) continue;
                posRef.current.set(n.id, { x: n.x, y: n.y, vx: n.vx, vy: n.vy });
              }
            }}
            linkVisibility={(l: GLink) => {
              if (!showSemanticLinks && isMaterialLink(l.reason)) return false;
              const [a, b] = linkEnds(l);
              return visibleIds.has(a) && visibleIds.has(b);
            }}
            linkColor={(l: GLink) => {
              const [a, b] = linkEnds(l);
              if (!visibleIds.has(a) || !visibleIds.has(b)) return "rgba(0,0,0,0)";
              if (!showSemanticLinks && isMaterialLink(l.reason)) return "rgba(0,0,0,0)";
              const lit = !hoverSet || (hoverSet.has(a) && hoverSet.has(b));
              if (hoverSet && !lit) return "rgba(255,255,255,0.03)";
              if (l.reason === "semantic") {
                return hoverSet && lit ? "rgba(110,231,183,0.8)" : "rgba(110,231,183,0.34)";
              }
              if (l.reason === "shared_tool") {
                return hoverSet && lit ? "rgba(251,191,36,0.85)" : "rgba(251,191,36,0.38)";
              }
              if (hoverSet && lit) return "rgba(255,255,255,0.55)";
              if (l.reason === "belongs") return "rgba(255,255,255,0.2)";
              return "rgba(255,255,255,0.1)";
            }}
            linkWidth={(l: GLink) => {
              const [a, b] = linkEnds(l);
              if (hoverSet && hoverSet.has(a) && hoverSet.has(b)) return 1.3;
              if (l.reason === "shared_tool" || l.reason === "semantic") return 0.9;
              return l.reason === "belongs" ? 0.8 : 0.4;
            }}
            onNodeHover={(n: GNode | null) => {
              if (n && !visibleIds.has(n.id)) {
                setHover(null);
                return;
              }
              setHover(n || null);
            }}
            onNodeClick={(n: GNode) => {
              if (!visibleIds.has(n.id)) return;
              selectNode(n);
            }}
            onBackgroundClick={() => setSheet(null)}
            nodeCanvasObject={(node: GNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
              const n = node;
              if (!visibleIds.has(n.id)) return;
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
              if (!visibleIds.has(node.id)) return;
              // Larger hit target on touch so a finger can grab a node reliably
              const r = nodeRadius(node) + (coarsePointer ? 14 : 7);
              ctx.beginPath();
              ctx.arc(node.x || 0, node.y || 0, r, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.fill();
            }}
          />
        )}

        {noMatches && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center">
            <p className="rounded-lg border border-white/10 bg-black/70 px-4 py-2 text-sm text-white/70 backdrop-blur-md">
              Нет совпадений
            </p>
          </div>
        )}

        {hover && !sheet && (
          <div
            className="graph-hover-tip pointer-events-none absolute z-30 w-[min(220px,70vw)] rounded-md border border-white/10 bg-black/85 px-3 py-2 shadow-xl"
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

      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 p-3 pt-[max(0.75rem,env(safe-area-inset-top))] md:p-4 md:pt-4 md:pr-52">
        <div className="pointer-events-auto flex max-w-md flex-col gap-2">
          <div ref={searchWrapRef} className="relative">
            <div className="flex items-center gap-2 rounded-full border border-white/10 bg-black/55 px-3 py-1.5 backdrop-blur-md">
              <input
                ref={searchInputRef}
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setSearchOpen(true);
                }}
                onFocus={() => setSearchOpen(true)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setSearchOpen(false);
                    (e.target as HTMLInputElement).blur();
                  } else if (e.key === "Enter" && searchHits[0]) {
                    e.preventDefault();
                    selectNode(searchHits[0]);
                    setSearchOpen(false);
                  }
                }}
                placeholder="Категория, тег или материал…"
                className="min-w-0 flex-1 bg-transparent py-1.5 text-base text-white outline-none placeholder:text-white/35 md:text-sm"
              />
              <button
                type="button"
                onClick={() => setFiltersOpen((v) => !v)}
                aria-expanded={filtersOpen}
                aria-label="Фильтры"
                className={`shrink-0 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                  filtersOpen || activeFilters
                    ? "bg-[var(--accent)] text-[var(--btn-fg)]"
                    : "bg-white/12 text-white/90 hover:bg-white/20"
                }`}
              >
                Фильтры{activeFilters ? " ·" : ""}
              </button>
            </div>
            {searchOpen && searchHits.length > 0 && (
              <ul className="graph-search-hits" role="listbox" aria-label="Результаты поиска">
                {searchHits.map((hit) => (
                  <li key={hit.id}>
                    <button
                      type="button"
                      role="option"
                      className="graph-search-hit"
                      onClick={() => {
                        selectNode(hit);
                        setQ(hit.title);
                        setSearchOpen(false);
                      }}
                    >
                      <span className="graph-search-hit-title">{hit.title}</span>
                      <span className="graph-search-hit-type">{nodeTypeLabel(hit.type)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {filtersOpen && (
            <div className="graph-filters">
              <div className="graph-filters-grid">
                <label className="graph-filter-field">
                  <span className="graph-filter-label">Категория</span>
                  <FancySelect
                    aria-label="Категория"
                    variant="graph"
                    value={category}
                    onChange={setCategory}
                    onSeekGlobal={focusSearch}
                    seekGlobalLabel="Искать в сети"
                    options={[
                      { value: "", label: "Все" },
                      ...categories.map((c) => ({ value: c, label: c })),
                    ]}
                  />
                </label>
                <label className="graph-filter-field">
                  <span className="graph-filter-label">Тег</span>
                  <FancySelect
                    aria-label="Тег"
                    variant="graph"
                    value={tag}
                    onChange={setTag}
                    onSeekGlobal={focusSearch}
                    seekGlobalLabel="Искать в сети"
                    options={[
                      { value: "", label: "Все" },
                      ...tags.map((t) => ({ value: t, label: t })),
                    ]}
                  />
                </label>
                <label className="graph-filter-field graph-filter-field--score">
                  <span className="graph-filter-label">Score</span>
                  <div className="graph-score-seg" role="radiogroup" aria-label="Минимальный score">
                    {[
                      { v: 0, l: "Все" },
                      { v: 5, l: "≥5" },
                      { v: 7, l: "≥7" },
                      { v: 8, l: "≥8" },
                    ].map((s) => (
                      <button
                        key={s.v}
                        type="button"
                        role="radio"
                        aria-checked={minScore === s.v}
                        className="graph-score-seg-item"
                        data-active={minScore === s.v}
                        onClick={() => setMinScore(s.v)}
                      >
                        {s.l}
                      </button>
                    ))}
                  </div>
                </label>
                <label className="graph-filter-field graph-filter-field--score">
                  <span className="graph-filter-label">Связи материалов</span>
                  <div className="graph-score-seg" role="radiogroup" aria-label="Связи по смыслу и инструментам">
                    <button
                      type="button"
                      role="radio"
                      aria-checked={showSemanticLinks}
                      className="graph-score-seg-item"
                      data-active={showSemanticLinks}
                      onClick={() => setShowSemanticLinks(true)}
                    >
                      Смысл + стеки
                    </button>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={!showSemanticLinks}
                      className="graph-score-seg-item"
                      data-active={!showSemanticLinks}
                      onClick={() => setShowSemanticLinks(false)}
                    >
                      Только темы
                    </button>
                  </div>
                </label>
              </div>
              {activeFilters && (
                <button
                  type="button"
                  className="graph-filters-clear"
                  onClick={() => {
                    setCategory("");
                    setTag("");
                    setMinScore(0);
                    setQ("");
                  }}
                >
                  Сбросить фильтры
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {sheet && (
        <aside className="graph-sheet absolute bottom-[var(--app-tabbar-h)] left-0 right-0 z-30 mx-auto flex max-h-[min(52dvh,28rem)] w-full flex-col md:bottom-6 md:left-auto md:right-4 md:max-h-[70dvh] md:w-[340px]">
          <div className="graph-sheet-panel flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="graph-sheet-handle md:hidden" aria-hidden />
            <div className="graph-sheet-bar">
              <button
                type="button"
                className="graph-sheet-icon"
                aria-label="Ко всем"
                title="Ко всем"
                onClick={() => clearSelection()}
              >
                ✕
              </button>
              <div className="graph-sheet-center">
                <p className="text-[10px] uppercase tracking-[0.2em] text-white/40">
                  {sheet.type === "material"
                    ? sheet.source_type
                    : sheet.type === "category"
                      ? "категория"
                      : "тег"}
                </p>
                <h2
                  className="mt-0.5 line-clamp-2 text-base leading-snug text-white md:text-lg"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  {sheet.title}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setSheet(null)}
                className="graph-sheet-back"
              >
                ← Назад
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
              {sheet.type === "material" ? (
                <>
                  <p className="text-sm text-white/50">
                    {sheet.category || "без категории"}
                    <span className="mx-1.5 opacity-40">·</span>
                    score {sheet.score ?? "—"}
                  </p>
                  {sheet.summary && (
                    <p className="mt-3 text-sm leading-relaxed text-white/80 line-clamp-6">{sheet.summary}</p>
                  )}
                  {relatedMaterialsOfSheet.length > 0 && (
                    <div className="mt-4 border-t border-white/10 pt-3">
                      <p className="text-[11px] uppercase tracking-[0.16em] text-white/45">
                        Связанные материалы
                      </p>
                      <ul className="mt-2 space-y-1">
                        {relatedMaterialsOfSheet.map(({ node, reason, badge }) => (
                          <li key={`${node.id}:${reason}`}>
                            <button
                              type="button"
                              className="flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left text-xs text-white/85 transition hover:bg-white/8 hover:text-white"
                              onClick={() => {
                                const target = graphData.nodes.find((x) => x.id === node.id);
                                if (target) selectNode(target);
                                else setSheet(node as GNode);
                              }}
                            >
                              <span className="min-w-0 line-clamp-1">{node.title}</span>
                              <span
                                className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${
                                  reason === "shared_tool"
                                    ? "bg-amber-400/15 text-amber-300"
                                    : "bg-emerald-400/15 text-emerald-300"
                                }`}
                              >
                                {badge}
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <button
                    type="button"
                    className="mt-5 w-full rounded-full border border-[var(--accent)] bg-[var(--accent)] px-4 py-3 text-sm font-medium text-[var(--btn-fg)] transition hover:brightness-110"
                    onClick={() => router.push(`/m/${sheet.id}`)}
                  >
                    Открыть материал
                  </button>
                </>
              ) : (
                <>
                  <p className="text-sm text-white/50">{neighborsOfSheet.length} материалов</p>
                  <ul className="mt-3 space-y-1">
                    {neighborsOfSheet.slice(0, 14).map((n) => (
                      <li key={n.id}>
                        <button
                          type="button"
                          className="flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-left text-sm text-white/85 transition hover:bg-white/8 hover:text-white"
                          onClick={() => {
                            const node = graphData.nodes.find((x) => x.id === n.id);
                            if (node) selectNode(node);
                            else setSheet(n as GNode);
                          }}
                        >
                          <span className="min-w-0 line-clamp-2">{n.title}</span>
                          <span className="shrink-0 text-white/35" aria-hidden>
                            ›
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </div>
        </aside>
      )}
    </section>
  );
}
