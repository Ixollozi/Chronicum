"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
} from "react";
import type {
  GraphCluster,
  GraphClusterEdge,
  GraphEdge,
  GraphNode,
} from "@/lib/api";

export type AtlasLodLevel = 1 | 2 | 3;
export type AtlasViewMode = "clusters" | "links" | "hygiene";

export function isUncategorizedMaterial(node: GraphNode): boolean {
  return (
    node.type !== "client" &&
    (!node.category || node.cluster_id === "cluster:uncategorized")
  );
}

export type KnowledgeAtlasHandle = {
  fitOverview: (instant?: boolean) => void;
  focusCluster: (clusterId: string) => void;
  focusNode: (nodeId: string, opts?: { isolate?: boolean }) => void;
  zoomBy: (factor: number) => void;
};

type Props = {
  clusters: GraphCluster[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  clusterEdges: GraphClusterEdge[];
  matchedNodeIds: Set<string>;
  hasActiveFilter: boolean;
  viewMode: AtlasViewMode;
  selectedClusterId: string | null;
  selectedNodeId: string | null;
  hoveredNodeId: string | null;
  hoveredClusterId: string | null;
  isolateEgo: boolean;
  inspectorOpen: boolean;
  onHoverNode: (nodeId: string | null) => void;
  onHoverCluster: (clusterId: string | null) => void;
  onSelectNode: (nodeId: string) => void;
  onOpenNode: (node: GraphNode) => void;
  onSelectCluster: (clusterId: string) => void;
  onClearSelection: () => void;
  onZoomStateChange?: (state: { lod: AtlasLodLevel; relZoom: number }) => void;
};

type Camera = { x: number; y: number; k: number };
type Rect = { x0: number; y0: number; x1: number; y1: number };
type PlacedClusterPill = { id: string; rect: Rect };
type AnimState = {
  from: Camera;
  to: Camera;
  start: number;
  duration: number;
};

const BG_BASE = "#0b0e0d";
const BG_VIGNETTE_INNER = "#141917";
const BG_VIGNETTE_OUTER = "#080a09";
const ACCENT_BRASS = "#c4a574";
const TEXT_INK = "#eef2ed";
const TEXT_MUTED = "#9ba59d";

export const EDGE_COLORS: Record<string, string> = {
  shared_tool: "#d4a65a",
  semantic: "#6db89b",
  client: "#c9886b",
  link: "#6ea3a8",
};

function hexToRgba(hex: string, alpha: number): string {
  const clean = hex.replace("#", "").trim();
  if (clean.length === 3) {
    const r = parseInt(clean[0]! + clean[0]!, 16);
    const g = parseInt(clean[1]! + clean[1]!, 16);
    const b = parseInt(clean[2]! + clean[2]!, 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  if (clean.length !== 6) return `rgba(196, 165, 116, ${alpha})`;
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function rectsOverlap(a: Rect, b: Rect, pad = 4): boolean {
  return !(
    a.x1 + pad < b.x0 ||
    a.x0 - pad > b.x1 ||
    a.y1 + pad < b.y0 ||
    a.y0 - pad > b.y1
  );
}

function pointInPolygon(px: number, py: number, poly: [number, number][]): boolean {
  if (poly.length < 3) return false;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i]![0];
    const yi = poly[i]![1];
    const xj = poly[j]![0];
    const yj = poly[j]![1];
    const intersect =
      yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi + 1e-9) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

function truncateText(text: string, maxLen: number): string {
  const trimmed = text.trim();
  if (trimmed.length <= maxLen) return trimmed;
  return `${trimmed.slice(0, Math.max(1, maxLen - 1)).trimEnd()}…`;
}

function compactCategoryPrefix(prefix: string, maxLen = 8): string {
  const trimmed = prefix.trim();
  if (trimmed.length <= maxLen) return trimmed;
  const firstWord = trimmed.split(/\s+/)[0] ?? trimmed;
  if (firstWord.length <= maxLen) return firstWord;
  let stem = firstWord.slice(0, Math.max(4, maxLen - 1));
  while (stem.length > 4 && /[аеёиоуыэюяaeiouy]$/i.test(stem)) {
    stem = stem.slice(0, -1);
  }
  return `${stem}.`;
}

function formatMacroClusterLabel(label: string, category: string | null): string {
  const trimmed = label.trim();
  let prefix: string | null = null;
  let rest: string | null = null;

  if (category && trimmed.startsWith(`${category} · `)) {
    prefix = category;
    rest = trimmed.slice(category.length + 3).trim();
  } else {
    const sepIdx = trimmed.indexOf(" · ");
    if (sepIdx > 0) {
      prefix = trimmed.slice(0, sepIdx).trim();
      rest = trimmed.slice(sepIdx + 3).trim();
    }
  }

  if (!prefix || !rest) {
    return truncateText(trimmed, 30);
  }

  if (trimmed.length <= 32) return trimmed;

  const shortPrefix = compactCategoryPrefix(prefix, 8);
  const suffixBudget = Math.max(24, 36 - shortPrefix.length - 3);
  return `${shortPrefix} · ${truncateText(rest, suffixBudget)}`;
}

function roundedRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  const rad = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.lineTo(x + w - rad, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rad);
  ctx.lineTo(x + w, y + h - rad);
  ctx.quadraticCurveTo(x + w, y + h, x + w - rad, y + h);
  ctx.lineTo(x + rad, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - rad);
  ctx.lineTo(x, y + rad);
  ctx.quadraticCurveTo(x, y, x + rad, y);
  ctx.closePath();
}

function traceSmoothHull(
  ctx: CanvasRenderingContext2D,
  pts: [number, number][]
) {
  const n = pts.length;
  if (n < 3) return;
  const last = pts[n - 1]!;
  const first = pts[0]!;
  ctx.beginPath();
  ctx.moveTo((last[0] + first[0]) / 2, (last[1] + first[1]) / 2);
  for (let i = 0; i < n; i++) {
    const curr = pts[i]!;
    const next = pts[(i + 1) % n]!;
    ctx.quadraticCurveTo(
      curr[0],
      curr[1],
      (curr[0] + next[0]) / 2,
      (curr[1] + next[1]) / 2
    );
  }
  ctx.closePath();
}

function drawDiamond(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  r: number
) {
  ctx.beginPath();
  ctx.moveTo(cx, cy - r);
  ctx.lineTo(cx + r, cy);
  ctx.lineTo(cx, cy + r);
  ctx.lineTo(cx - r, cy);
  ctx.closePath();
}

function baseNodeRadius(node: GraphNode): number {
  if (node.type === "client") return 7.8;
  const s = node.score;
  if (s !== null && s >= 8) return 5.5;
  if (s !== null && s >= 5) return 3.8;
  return 2.5;
}

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

const KnowledgeAtlasCanvas = forwardRef<KnowledgeAtlasHandle, Props>(
  function KnowledgeAtlasCanvas(props, ref) {
    const {
      clusters,
      nodes,
      edges,
      clusterEdges,
      matchedNodeIds,
      hasActiveFilter,
      viewMode,
      selectedClusterId,
      selectedNodeId,
      hoveredNodeId,
      hoveredClusterId,
      isolateEgo,
      inspectorOpen,
      onHoverNode,
      onHoverCluster,
      onSelectNode,
      onOpenNode,
      onSelectCluster,
      onClearSelection,
    } = props;

    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const containerRef = useRef<HTMLDivElement | null>(null);

    const sizeRef = useRef({ width: 1200, height: 800, dpr: 1 });
    const camRef = useRef<Camera>({ x: 0, y: 0, k: 0.85 });
    const overviewScaleRef = useRef<number>(0.85);
    const animRef = useRef<AnimState | null>(null);
    const rafRef = useRef<number | null>(null);
    const fittedOnceRef = useRef(false);
    const lastLodReportRef = useRef<{ lod: AtlasLodLevel; relZoom: number }>({
      lod: 1,
      relZoom: 1,
    });
    const placedClusterPillsRef = useRef<PlacedClusterPill[]>([]);
    const placedClientPillsRef = useRef<PlacedClusterPill[]>([]);

    // Pointer / gesture state
    const pointerRef = useRef<{
      active: boolean;
      dragging: boolean;
      startX: number;
      startY: number;
      camStartX: number;
      camStartY: number;
      lastTapTime: number;
      lastTapNodeId: string | null;
      pointers: Map<number, { x: number; y: number }>;
      pinchDist: number;
      pinchCamK: number;
    }>({
      active: false,
      dragging: false,
      startX: 0,
      startY: 0,
      camStartX: 0,
      camStartY: 0,
      lastTapTime: 0,
      lastTapNodeId: null,
      pointers: new Map(),
      pinchDist: 0,
      pinchCamK: 1,
    });

    // Latest props ref so the rAF draw loop always sees current data cleanly
    const stateRef = useRef(props);
    stateRef.current = props;

    // Pre-indexed maps & adjacency for fast rendering + ego-focus
    const indexed = useMemo(() => {
      const clusterById = new Map<string, GraphCluster>();
      const clusterColorById = new Map<string, string>();
      const subclustersByCategory = new Map<string, GraphCluster[]>();

      for (const c of clusters) {
        clusterById.set(c.id, c);
        clusterColorById.set(c.id, c.color || "#6c7570");
        if (c.category) {
          const list = subclustersByCategory.get(c.category) || [];
          list.push(c);
          subclustersByCategory.set(c.category, list);
        }
      }

      const nodeById = new Map<string, GraphNode>();
      const hygieneNodeIds = new Set<string>();
      const hygieneClusterIds = new Set<string>();
      const clientNodes: GraphNode[] = [];
      for (const n of nodes) {
        nodeById.set(n.id, n);
        if (n.type === "client") {
          clientNodes.push(n);
        } else if (isUncategorizedMaterial(n)) {
          hygieneNodeIds.add(n.id);
          if (n.cluster_id) hygieneClusterIds.add(n.cluster_id);
        }
      }
      clientNodes.sort(
        (a, b) => b.degree - a.degree || a.title.localeCompare(b.title, "ru")
      );

      const adj = new Map<string, Set<string>>();
      const edgesByNode = new Map<string, GraphEdge[]>();
      for (const e of edges) {
        if (!nodeById.has(e.source) || !nodeById.has(e.target)) continue;
        if (!adj.has(e.source)) adj.set(e.source, new Set());
        if (!adj.has(e.target)) adj.set(e.target, new Set());
        adj.get(e.source)!.add(e.target);
        adj.get(e.target)!.add(e.source);

        if (!edgesByNode.has(e.source)) edgesByNode.set(e.source, []);
        if (!edgesByNode.has(e.target)) edgesByNode.set(e.target, []);
        edgesByNode.get(e.source)!.push(e);
        edgesByNode.get(e.target)!.push(e);
      }

      // World bounding box across all cluster hulls and nodes
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      for (const c of clusters) {
        if (c.hull && c.hull.length >= 3) {
          for (const [hx, hy] of c.hull) {
            if (hx < minX) minX = hx;
            if (hx > maxX) maxX = hx;
            if (hy < minY) minY = hy;
            if (hy > maxY) maxY = hy;
          }
        } else {
          minX = Math.min(minX, c.x - c.radius);
          maxX = Math.max(maxX, c.x + c.radius);
          minY = Math.min(minY, c.y - c.radius);
          maxY = Math.max(maxY, c.y + c.radius);
        }
      }
      for (const n of nodes) {
        const nx = n.x ?? 0;
        const ny = n.y ?? 0;
        if (nx < minX) minX = nx;
        if (nx > maxX) maxX = nx;
        if (ny < minY) minY = ny;
        if (ny > maxY) maxY = ny;
      }
      if (!Number.isFinite(minX)) {
        minX = -400;
        maxX = 400;
        minY = -300;
        maxY = 300;
      }

      // Sort materials once by editorial prominence for fast LOD label selection
      const nodesByImportance = [...nodes].sort((a, b) => {
        const aClient = a.type === "client" ? 1 : 0;
        const bClient = b.type === "client" ? 1 : 0;
        if (aClient !== bClient) return bClient - aClient;
        const sa = a.score ?? -1;
        const sb = b.score ?? -1;
        if (sa !== sb) return sb - sa;
        if (a.degree !== b.degree) return b.degree - a.degree;
        return a.title.localeCompare(b.title, "ru");
      });

      // Top inter-cluster bridges for subtle macro context
      const topClusterEdges = [...clusterEdges]
        .sort((a, b) => b.weight - a.weight)
        .slice(0, 8);

      return {
        clusterById,
        clusterColorById,
        subclustersByCategory,
        nodeById,
        hygieneNodeIds,
        hygieneClusterIds,
        clientNodes,
        adj,
        edgesByNode,
        bounds: { minX, maxX, minY, maxY },
        nodesByImportance,
        topClusterEdges,
      };
    }, [clusters, nodes, edges, clusterEdges]);

    const indexedRef = useRef(indexed);
    indexedRef.current = indexed;

    const worldToScreen = useCallback((wx: number, wy: number, cam = camRef.current) => {
      const { width, height } = sizeRef.current;
      return {
        x: (wx - cam.x) * cam.k + width / 2,
        y: (wy - cam.y) * cam.k + height / 2,
      };
    }, []);

    const screenToWorld = useCallback((sx: number, sy: number, cam = camRef.current) => {
      const { width, height } = sizeRef.current;
      return {
        x: (sx - width / 2) / cam.k + cam.x,
        y: (sy - height / 2) / cam.k + cam.y,
      };
    }, []);

    const computeOverviewCamera = useCallback((): Camera => {
      const { width, height } = sizeRef.current;
      const { minX, maxX, minY, maxY } = indexedRef.current.bounds;
      const spanX = Math.max(240, maxX - minX);
      const spanY = Math.max(200, maxY - minY);
      const isDesktop = width >= 768;
      const padTop = isDesktop ? 86 : 112;
      const padBottom = isDesktop ? 56 : 116;
      const padLeft = isDesktop ? 44 : 24;
      const padRight = isDesktop && stateRef.current.inspectorOpen ? 390 : isDesktop ? 44 : 24;

      const availW = Math.max(200, width - padLeft - padRight);
      const availH = Math.max(200, height - padTop - padBottom);
      const k = Math.min(availW / spanX, availH / spanY) * 0.9;
      const clampedK = Math.max(0.24, Math.min(1.6, k));
      overviewScaleRef.current = clampedK;

      const cx = (minX + maxX) / 2;
      const cy = (minY + maxY) / 2;
      const offsetX = (padLeft - padRight) / 2 / clampedK;
      const offsetY = (padTop - padBottom) / 2 / clampedK;

      return { x: cx - offsetX, y: cy - offsetY, k: clampedK };
    }, []);

    const drawRef = useRef<() => void>(() => {});

    const scheduleDraw = useCallback(() => {
      if (rafRef.current !== null) return;
      rafRef.current = requestAnimationFrame(function frame(now) {
        rafRef.current = null;
        if (animRef.current) {
          const anim = animRef.current;
          const t = Math.min(1, (now - anim.start) / anim.duration);
          const e = easeOutCubic(t);
          camRef.current = {
            x: anim.from.x + (anim.to.x - anim.from.x) * e,
            y: anim.from.y + (anim.to.y - anim.from.y) * e,
            k: anim.from.k + (anim.to.k - anim.from.k) * e,
          };
          if (t >= 1) {
            animRef.current = null;
          } else {
            rafRef.current = requestAnimationFrame(frame);
          }
        }
        drawRef.current();
      });
    }, []);

    const animateCameraTo = useCallback(
      (target: Camera, instant = false) => {
        const reduced =
          typeof window !== "undefined" &&
          window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (instant || reduced) {
          animRef.current = null;
          camRef.current = target;
          scheduleDraw();
          return;
        }
        animRef.current = {
          from: { ...camRef.current },
          to: target,
          start: performance.now(),
          duration: 380,
        };
        scheduleDraw();
      },
      [scheduleDraw]
    );

    const fitOverview = useCallback(
      (instant = false) => {
        const cam = computeOverviewCamera();
        animateCameraTo(cam, instant);
      },
      [computeOverviewCamera, animateCameraTo]
    );

    const focusCluster = useCallback(
      (clusterId: string) => {
        const c = indexedRef.current.clusterById.get(clusterId);
        if (!c) return;
        const { width, height } = sizeRef.current;
        const isDesktop = width >= 768;
        const rightReserve = isDesktop ? 375 : 0;
        const topReserve = isDesktop ? 84 : 112;
        const bottomReserve = isDesktop ? 48 : 240;
        const availW = Math.max(240, width - rightReserve - 60);
        const availH = Math.max(220, height - topReserve - bottomReserve);

        let minX = c.x - c.radius;
        let maxX = c.x + c.radius;
        let minY = c.y - c.radius;
        let maxY = c.y + c.radius;
        if (c.hull && c.hull.length >= 3) {
          minX = Infinity;
          maxX = -Infinity;
          minY = Infinity;
          maxY = -Infinity;
          for (const [hx, hy] of c.hull) {
            if (hx < minX) minX = hx;
            if (hx > maxX) maxX = hx;
            if (hy < minY) minY = hy;
            if (hy > maxY) maxY = hy;
          }
        }
        const spanX = Math.max(110, maxX - minX);
        const spanY = Math.max(110, maxY - minY);
        const fitK = Math.min(availW / spanX, availH / spanY) * 0.78;
        const minMesoK = overviewScaleRef.current * 1.6;
        const k = Math.max(minMesoK, Math.min(3.0, fitK));
        const cx = (minX + maxX) / 2;
        const cy = (minY + maxY) / 2;
        const offsetX = -rightReserve / 2 / k;
        const offsetY = (topReserve - bottomReserve) / 2 / k;
        animateCameraTo({ x: cx - offsetX, y: cy - offsetY, k });
      },
      [animateCameraTo]
    );

    const focusNode = useCallback(
      (nodeId: string, opts?: { isolate?: boolean }) => {
        const n = indexedRef.current.nodeById.get(nodeId);
        if (!n) return;
        const { width, height } = sizeRef.current;
        const isDesktop = width >= 768;
        const rightReserve = isDesktop ? 375 : 0;
        const topReserve = isDesktop ? 84 : 112;
        const bottomReserve = isDesktop ? 48 : 260;

        const nx = n.x ?? 0;
        const ny = n.y ?? 0;

        if (opts?.isolate) {
          const neigh = indexedRef.current.adj.get(nodeId);
          let minX = nx;
          let maxX = nx;
          let minY = ny;
          let maxY = ny;
          if (neigh && neigh.size > 0) {
            for (const nid of neigh) {
              const m = indexedRef.current.nodeById.get(nid);
              if (!m) continue;
              const mx = m.x ?? 0;
              const my = m.y ?? 0;
              minX = Math.min(minX, mx);
              maxX = Math.max(maxX, mx);
              minY = Math.min(minY, my);
              maxY = Math.max(maxY, my);
            }
          }
          const availW = Math.max(240, width - rightReserve - 90);
          const availH = Math.max(220, height - topReserve - bottomReserve);
          const spanX = Math.max(130, maxX - minX + 100);
          const spanY = Math.max(130, maxY - minY + 100);
          const k = Math.max(
            overviewScaleRef.current * 1.4,
            Math.min(2.8, Math.min(availW / spanX, availH / spanY) * 0.85)
          );
          const offsetX = -rightReserve / 2 / k;
          const offsetY = (topReserve - bottomReserve) / 2 / k;
          animateCameraTo({
            x: (minX + maxX) / 2 - offsetX,
            y: (minY + maxY) / 2 - offsetY,
            k,
          });
          return;
        }

        const targetK = Math.max(camRef.current.k, overviewScaleRef.current * 2.15);
        const k = Math.min(3.4, targetK);
        const offsetX = -rightReserve / 2 / k;
        const offsetY = (topReserve - bottomReserve) / 2 / k;
        animateCameraTo({ x: nx - offsetX, y: ny - offsetY, k });
      },
      [animateCameraTo]
    );

    const zoomBy = useCallback(
      (factor: number) => {
        const cur = camRef.current;
        const nextK = Math.max(
          overviewScaleRef.current * 0.55,
          Math.min(4.8, cur.k * factor)
        );
        animateCameraTo({ x: cur.x, y: cur.y, k: nextK });
      },
      [animateCameraTo]
    );

    useImperativeHandle(
      ref,
      () => ({
        fitOverview,
        focusCluster,
        focusNode,
        zoomBy,
      }),
      [fitOverview, focusCluster, focusNode, zoomBy]
    );

    const draw = useCallback(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const { width, height, dpr } = sizeRef.current;
      if (width <= 0 || height <= 0) return;

      const {
        clusters: curClusters,
        nodes: curNodes,
        edges: curEdges,
        matchedNodeIds: curMatched,
        hasActiveFilter: curFiltered,
        viewMode: curMode,
        selectedClusterId: selCluster,
        selectedNodeId: selNode,
        hoveredNodeId: hovNode,
        hoveredClusterId: hovCluster,
        isolateEgo: curIsolate,
        onZoomStateChange: reportZoom,
      } = stateRef.current;

      const idx = indexedRef.current;
      const cam = camRef.current;
      const relZoom = cam.k / Math.max(0.05, overviewScaleRef.current);

      let lod: AtlasLodLevel = 1;
      if (relZoom >= 2.35 || selNode) lod = 3;
      else if (relZoom >= 1.35 || selCluster) lod = 2;

      if (
        reportZoom &&
        (lastLodReportRef.current.lod !== lod ||
          Math.abs(lastLodReportRef.current.relZoom - relZoom) > 0.08)
      ) {
        lastLodReportRef.current = { lod, relZoom };
        reportZoom({ lod, relZoom });
      }

      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // 1. Warm dark archive background + subtle radial spotlight
      ctx.fillStyle = BG_BASE;
      ctx.fillRect(0, 0, width, height);

      const vig = ctx.createRadialGradient(
        width * 0.48,
        height * 0.46,
        Math.min(width, height) * 0.08,
        width * 0.5,
        height * 0.5,
        Math.max(width, height) * 0.78
      );
      vig.addColorStop(0, BG_VIGNETTE_INNER);
      vig.addColorStop(0.65, BG_BASE);
      vig.addColorStop(1, BG_VIGNETTE_OUTER);
      ctx.fillStyle = vig;
      ctx.fillRect(0, 0, width, height);

      // Subtle cartographic dot grid (adapts to zoom)
      const baseStep = cam.k < 0.7 ? 96 : cam.k < 1.6 ? 56 : 32;
      const stepPx = baseStep * cam.k;
      if (stepPx >= 18) {
        const topLeft = screenToWorld(0, 0, cam);
        const botRight = screenToWorld(width, height, cam);
        const startX = Math.floor(topLeft.x / baseStep) * baseStep;
        const startY = Math.floor(topLeft.y / baseStep) * baseStep;
        ctx.fillStyle = "rgba(231, 235, 230, 0.042)";
        for (let wx = startX; wx <= botRight.x; wx += baseStep) {
          const sx = (wx - cam.x) * cam.k + width / 2;
          for (let wy = startY; wy <= botRight.y; wy += baseStep) {
            const sy = (wy - cam.y) * cam.k + height / 2;
            ctx.fillRect(sx - 0.7, sy - 0.7, 1.4, 1.4);
          }
        }
      }

      // Active ego node (hover takes temporary focus, click persists)
      const egoId = hovNode || selNode;
      const egoNeighbors = egoId ? idx.adj.get(egoId) || new Set<string>() : null;
      const selectedClusterObj = selCluster ? idx.clusterById.get(selCluster) : null;
      const selectedCategory = selectedClusterObj?.category ?? null;
      const hygieneActive =
        curMode === "hygiene" && idx.hygieneNodeIds.size > 0;

      // World-space transform for hulls, edges, and nodes
      ctx.save();
      ctx.translate(width / 2, height / 2);
      ctx.scale(cam.k, cam.k);
      ctx.translate(-cam.x, -cam.y);

      // 2. Cluster territory halos (soft organic rounded hulls)
      for (const c of curClusters) {
        const isSelected = selCluster === c.id;
        const isHovered = hovCluster === c.id;
        const isSiblingSubcluster =
          Boolean(selectedCategory) &&
          c.category === selectedCategory &&
          (idx.subclustersByCategory.get(selectedCategory!)?.length ?? 0) > 1;
        const isUncat = c.id === "cluster:uncategorized";

        let dimmed = false;
        if (egoId) {
          const egoNodeObj = idx.nodeById.get(egoId);
          dimmed = egoNodeObj?.cluster_id !== c.id;
        } else if (selCluster && !isSelected && !isSiblingSubcluster) {
          dimmed = true;
        } else if (hygieneActive && !idx.hygieneClusterIds.has(c.id)) {
          dimmed = true;
        }

        const fillAlpha = isSelected
          ? 0.115
          : isHovered
            ? 0.095
            : dimmed
              ? 0.022
              : isSiblingSubcluster
                ? 0.075
                : 0.055;
        const strokeAlpha = isSelected
          ? 0.52
          : isHovered
            ? 0.36
            : dimmed
              ? 0.07
              : isSiblingSubcluster
                ? 0.28
                : 0.2;

        ctx.save();
        if (c.hull && c.hull.length >= 3) {
          traceSmoothHull(ctx, c.hull);
        } else {
          ctx.beginPath();
          ctx.arc(c.x, c.y, Math.max(36, c.radius), 0, Math.PI * 2);
        }

        const radGrad = ctx.createRadialGradient(
          c.x,
          c.y,
          4,
          c.x,
          c.y,
          Math.max(54, c.radius * 1.15)
        );
        radGrad.addColorStop(0, hexToRgba(c.color, fillAlpha * 1.45));
        radGrad.addColorStop(1, hexToRgba(c.color, fillAlpha * 0.45));
        ctx.fillStyle = radGrad;
        ctx.fill();

        if (isUncat) {
          ctx.setLineDash([6 / cam.k, 5 / cam.k]);
        }
        ctx.strokeStyle = hexToRgba(c.color, strokeAlpha);
        ctx.lineWidth = (isSelected ? 1.8 : isHovered ? 1.4 : 1.05) / cam.k;
        ctx.stroke();
        ctx.restore();
      }

      // 3. Subtle inter-cluster bridges on Macro (and when viewing sibling subclusters)
      if (!egoId && (lod === 1 || curMode === "clusters")) {
        ctx.save();
        ctx.setLineDash([4 / cam.k, 6 / cam.k]);
        for (const ce of idx.topClusterEdges) {
          const src = idx.clusterById.get(ce.source);
          const dst = idx.clusterById.get(ce.target);
          if (!src || !dst) continue;
          const touchesFocused =
            !selCluster || selCluster === src.id || selCluster === dst.id;
          const sameBigCat =
            Boolean(src.category) && src.category === dst.category;
          const alpha =
            selCluster && !touchesFocused
              ? 0.03
              : sameBigCat && lod >= 2
                ? 0.22
                : lod === 1
                  ? 0.13
                  : 0.07;

          const mx = (src.x + dst.x) / 2;
          const my = (src.y + dst.y) / 2;
          const dx = dst.x - src.x;
          const dy = dst.y - src.y;
          const bend = 0.08;
          const cx = mx - dy * bend;
          const cy = my + dx * bend;

          ctx.beginPath();
          ctx.moveTo(src.x, src.y);
          ctx.quadraticCurveTo(cx, cy, dst.x, dst.y);
          ctx.strokeStyle =
            sameBigCat && lod >= 2
              ? hexToRgba(ACCENT_BRASS, alpha)
              : `rgba(231, 235, 230, ${alpha})`;
          ctx.lineWidth =
            Math.min(2.2, 0.8 + Math.log1p(ce.shared_edges) * 0.45) / cam.k;
          ctx.stroke();
        }
        ctx.restore();
      }

      // 4. Material / Client edges — HIDDEN AT REST on Macro!
      const edgeMidLabels: {
        wx: number;
        wy: number;
        text: string;
        color: string;
      }[] = [];

      const drawCurvedEdge = (
        e: GraphEdge,
        alpha: number,
        widthPx: number,
        recordLabel: boolean
      ) => {
        const s = idx.nodeById.get(e.source);
        const t = idx.nodeById.get(e.target);
        if (!s || !t) return;
        const sx = s.x ?? 0;
        const sy = s.y ?? 0;
        const tx = t.x ?? 0;
        const ty = t.y ?? 0;
        const dx = tx - sx;
        const dy = ty - sy;
        const dist = Math.hypot(dx, dy) || 1;
        const curve = Math.min(34, dist * 0.12);
        const mx = (sx + tx) / 2 - (dy / dist) * curve;
        const my = (sy + ty) / 2 + (dx / dist) * curve;

        const color = EDGE_COLORS[e.reason] || "#8a938c";
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.quadraticCurveTo(mx, my, tx, ty);
        ctx.strokeStyle = hexToRgba(color, alpha);
        ctx.lineWidth = widthPx / cam.k;
        ctx.stroke();

        if (recordLabel) {
          const text =
            e.reason === "shared_tool" && e.label
              ? e.label
              : e.reason === "semantic" && typeof e.similarity === "number"
                ? `${Math.round(e.similarity * 100)}%`
                : e.reason === "client"
                  ? "клиент"
                  : null;
          if (text) {
            edgeMidLabels.push({
              wx: 0.25 * sx + 0.5 * mx + 0.25 * tx,
              wy: 0.25 * sy + 0.5 * my + 0.25 * ty,
              text,
              color,
            });
          }
        }
      };

      if (egoId) {
        // 1-hop Ego-Focus edges
        const incident: GraphEdge[] = idx.edgesByNode.get(egoId) || [];
        for (const e of incident) {
          const otherId: string = e.source === egoId ? e.target : e.source;
          if (curFiltered && !curMatched.has(otherId) && otherId !== egoId) continue;
          const isPrimary = e.source === selNode || e.target === selNode;
          drawCurvedEdge(e, isPrimary ? 0.78 : 0.68, isPrimary ? 1.75 : 1.5, true);
        }
      } else if (curMode === "links") {
        // Explicit "Связи" inspection mode: soft low-alpha edges across matched nodes
        for (const e of curEdges) {
          if (curFiltered && (!curMatched.has(e.source) || !curMatched.has(e.target)))
            continue;
          drawCurvedEdge(e, 0.26, 1.0, false);
        }
      } else if (lod >= 2) {
        // Meso / Micro without ego selection: only gentle high-confidence local edges
        for (const e of curEdges) {
          const s = idx.nodeById.get(e.source);
          const t = idx.nodeById.get(e.target);
          if (!s || !t) continue;
          if (curFiltered && (!curMatched.has(e.source) || !curMatched.has(e.target)))
            continue;
          if (selCluster && s.cluster_id !== selCluster && t.cluster_id !== selCluster)
            continue;
          const strong =
            e.reason === "client" ||
            e.reason === "link" ||
            e.reason === "shared_tool" ||
            (e.similarity ?? 0) >= 0.62;
          if (!strong) continue;
          const intraCluster = s.cluster_id === t.cluster_id;
          if (!intraCluster && lod < 3) continue;
          drawCurvedEdge(e, lod === 3 ? 0.24 : 0.16, 0.95, false);
        }
      }

      // 5. Nodes (Materials = mineral circles sized by score; Clients = brass diamonds)
      for (const n of curNodes) {
        const nx = n.x ?? 0;
        const ny = n.y ?? 0;
        const isMatched = !curFiltered || curMatched.has(n.id);
        const isSel = selNode === n.id;
        const isHov = hovNode === n.id;
        const isEgoNeighbor = Boolean(egoNeighbors && egoNeighbors.has(n.id));
        const inSelectedCluster =
          !selCluster || n.cluster_id === selCluster;
        const isHygieneTarget =
          hygieneActive && idx.hygieneNodeIds.has(n.id);

        let alpha = 0.92;
        if (!isMatched) {
          alpha = 0.08;
        } else if (egoId) {
          if (isSel || isHov) alpha = 1.0;
          else if (isEgoNeighbor) alpha = 0.96;
          else alpha = curIsolate ? 0.04 : 0.12;
        } else if (hygieneActive) {
          alpha = isHygieneTarget ? 0.98 : 0.14;
        } else if (selCluster && !inSelectedCluster) {
          alpha = 0.2;
        }

        const clusterColor =
          (n.cluster_id && idx.clusterColorById.get(n.cluster_id)) || "#6c7570";
        const baseR = baseNodeRadius(n);
        // Keep dots readable in screen space without bloating when zoomed far in
        const rWorld = baseR / Math.pow(cam.k, 0.72);

        if (n.type === "client") {
          // Prominent brass diamond marker for clients (clearly visible on Macro)
          ctx.save();
          const rDiamond = rWorld * (lod === 1 ? 1.15 : 1.0);
          if (alpha > 0.35) {
            const glow = ctx.createRadialGradient(
              nx,
              ny,
              rDiamond * 0.4,
              nx,
              ny,
              rDiamond * 2.5
            );
            glow.addColorStop(
              0,
              hexToRgba(ACCENT_BRASS, (isSel || isHov ? 0.38 : 0.22) * alpha)
            );
            glow.addColorStop(1, hexToRgba(ACCENT_BRASS, 0));
            ctx.beginPath();
            ctx.arc(nx, ny, rDiamond * 2.5, 0, Math.PI * 2);
            ctx.fillStyle = glow;
            ctx.fill();

            drawDiamond(ctx, nx, ny, rDiamond * (isSel || isHov ? 1.85 : 1.48));
            if (isSel || isHov) {
              ctx.fillStyle = hexToRgba(ACCENT_BRASS, 0.22);
              ctx.fill();
            }
            ctx.strokeStyle = hexToRgba(
              ACCENT_BRASS,
              (isSel || isHov ? 0.95 : 0.58) * alpha
            );
            ctx.lineWidth = (isSel || isHov ? 1.5 : 1.15) / cam.k;
            ctx.stroke();
          }
          drawDiamond(ctx, nx, ny, rDiamond);
          ctx.fillStyle = hexToRgba(ACCENT_BRASS, alpha);
          ctx.fill();
          ctx.strokeStyle = hexToRgba("#f7ebd5", alpha * 0.92);
          ctx.lineWidth = 1.25 / cam.k;
          ctx.stroke();

          ctx.beginPath();
          ctx.arc(nx, ny, rDiamond * 0.25, 0, Math.PI * 2);
          ctx.fillStyle = hexToRgba(BG_BASE, alpha);
          ctx.fill();
          ctx.restore();
          continue;
        }

        // Material circle
        if (isSel || isHov) {
          ctx.beginPath();
          ctx.arc(nx, ny, rWorld + 5.5 / cam.k, 0, Math.PI * 2);
          ctx.fillStyle = hexToRgba(ACCENT_BRASS, 0.2);
          ctx.fill();
          ctx.beginPath();
          ctx.arc(nx, ny, rWorld + 3.2 / cam.k, 0, Math.PI * 2);
          ctx.strokeStyle = hexToRgba(ACCENT_BRASS, 0.95);
          ctx.lineWidth = 1.5 / cam.k;
          ctx.stroke();
        } else if (isEgoNeighbor && egoId) {
          ctx.beginPath();
          ctx.arc(nx, ny, rWorld + 2.6 / cam.k, 0, Math.PI * 2);
          ctx.strokeStyle = hexToRgba(clusterColor, 0.65);
          ctx.lineWidth = 1.1 / cam.k;
          ctx.stroke();
        }

        ctx.beginPath();
        ctx.arc(nx, ny, rWorld, 0, Math.PI * 2);
        const dotColor = isHygieneTarget ? "#d08a62" : clusterColor;
        ctx.fillStyle = hexToRgba(dotColor, alpha);
        ctx.fill();

        // High-score materials (>= 8) get a crisp subtle warm rim
        if ((n.score ?? 0) >= 8 && alpha > 0.4) {
          ctx.strokeStyle = hexToRgba("#f4f1ea", alpha * 0.55);
          ctx.lineWidth = 1.0 / cam.k;
          ctx.stroke();
        }
      }

      ctx.restore(); // back to crisp screen-pixel coordinates!

      // 6. Screen-space Pill Labels with strict AABB Collision-Avoidance
      const placedBoxes: Rect[] = [];
      const clusterPillsHit: PlacedClusterPill[] = [];
      const clientPillsHit: PlacedClusterPill[] = [];

      const tryPlaceBox = (rect: Rect, force = false, pad = 5): boolean => {
        if (!force) {
          if (
            rect.x1 < 8 ||
            rect.x0 > width - 8 ||
            rect.y1 < 8 ||
            rect.y0 > height - 8
          ) {
            return false;
          }
          for (const b of placedBoxes) {
            if (rectsOverlap(rect, b, pad)) return false;
          }
        }
        placedBoxes.push(rect);
        return true;
      };

      const drawPill = (opts: {
        cx: number;
        cy: number;
        text: string;
        count?: string;
        dotColor?: string;
        dotShape?: "circle" | "diamond";
        borderColor: string;
        bgAlpha?: number;
        fontSize: number;
        fontWeight?: number;
        textColor?: string;
        force?: boolean;
        pad?: number;
        alignTop?: boolean;
      }): Rect | null => {
        const fontWeight = opts.fontWeight ?? 500;
        ctx.font = `${fontWeight} ${opts.fontSize}px var(--font-figtree), "Segoe UI", system-ui, sans-serif`;
        const textW = ctx.measureText(opts.text).width;
        const countW = opts.count ? ctx.measureText(opts.count).width + 8 : 0;
        const dotW = opts.dotColor ? 11 : 0;
        const padX = opts.fontSize >= 12 ? 9 : 7;
        const h = opts.fontSize + 9;
        const w = padX * 2 + dotW + textW + countW;

        const x0 = opts.cx - w / 2;
        const y0 = opts.alignTop ? opts.cy : opts.cy - h / 2;
        const rect: Rect = { x0, y0, x1: x0 + w, y1: y0 + h };

        if (!tryPlaceBox(rect, opts.force ?? false, opts.pad ?? 5)) {
          return null;
        }

        roundedRectPath(ctx, x0, y0, w, h, h / 2);
        ctx.fillStyle = `rgba(12, 15, 14, ${opts.bgAlpha ?? 0.88})`;
        ctx.fill();
        ctx.strokeStyle = opts.borderColor;
        ctx.lineWidth = 1;
        ctx.stroke();

        let cursorX = x0 + padX;
        const midY = y0 + h / 2;

        if (opts.dotColor) {
          if (opts.dotShape === "diamond") {
            drawDiamond(ctx, cursorX + 3.5, midY, 3.8);
            ctx.fillStyle = opts.dotColor;
            ctx.fill();
            ctx.strokeStyle = "rgba(247, 235, 213, 0.8)";
            ctx.lineWidth = 0.7;
            ctx.stroke();
          } else {
            ctx.beginPath();
            ctx.arc(cursorX + 3, midY, 3.2, 0, Math.PI * 2);
            ctx.fillStyle = opts.dotColor;
            ctx.fill();
          }
          cursorX += dotW;
        }

        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        ctx.fillStyle = opts.textColor ?? TEXT_INK;
        ctx.fillText(opts.text, cursorX, midY + 0.5);
        cursorX += textW;

        if (opts.count) {
          ctx.fillStyle = TEXT_MUTED;
          ctx.fillText(opts.count, cursorX + 6, midY + 0.5);
        }

        return rect;
      };

      // Reserve HUD corner areas on desktop so pills never clip under chrome
      const isDesktop = width >= 768;
      placedBoxes.push({
        x0: 0,
        y0: 0,
        x1: Math.min(isDesktop ? 460 : width - 16, width * 0.56),
        y1: isDesktop ? 76 : 108,
      });
      if (isDesktop) {
        placedBoxes.push({ x0: width - 390, y0: 0, x1: width, y1: 64 });
        if (stateRef.current.inspectorOpen) {
          placedBoxes.push({ x0: width - 376, y0: 64, x1: width, y1: height });
        }
      }

      // 6a. Priority 1: Selected and Hovered node pills (always rendered first)
      const renderNodePill = (node: GraphNode, priority: "active" | "neighbor" | "ambient") => {
        const pt = worldToScreen(node.x ?? 0, node.y ?? 0, cam);
        const rScreen = baseNodeRadius(node) * Math.pow(cam.k, 0.28);
        const clusterColor =
          (node.cluster_id && idx.clusterColorById.get(node.cluster_id)) ||
          ACCENT_BRASS;
        const isClient = node.type === "client";
        const border =
          priority === "active" || isClient
            ? hexToRgba(ACCENT_BRASS, 0.82)
            : hexToRgba(clusterColor, priority === "neighbor" ? 0.55 : 0.34);

        const maxLen = priority === "active" ? 44 : priority === "neighbor" ? 30 : 26;
        const label = truncateText(node.title, maxLen);
        const count =
          isClient
            ? "клиент"
            : node.score !== null && priority !== "ambient"
              ? `${node.score}`
              : undefined;

        return drawPill({
          cx: pt.x,
          cy: pt.y - rScreen - 13,
          text: label,
          count,
          dotColor: isClient ? ACCENT_BRASS : clusterColor,
          dotShape: isClient ? "diamond" : "circle",
          borderColor: border,
          bgAlpha: priority === "active" ? 0.95 : 0.88,
          fontSize: priority === "active" ? 12 : 11,
          fontWeight: priority === "active" ? 600 : 500,
          force: priority === "active",
          pad: priority === "neighbor" ? 4 : 6,
        });
      };

      if (selNode) {
        const sn = idx.nodeById.get(selNode);
        if (sn) renderNodePill(sn, "active");
      }
      if (hovNode && hovNode !== selNode) {
        const hn = idx.nodeById.get(hovNode);
        if (hn) renderNodePill(hn, "active");
      }

      // 6b. Priority 2: 1-hop neighbor pills + edge midpoint badges in ego-focus
      if (egoId && egoNeighbors) {
        const neighbors = [...egoNeighbors]
          .map((id) => idx.nodeById.get(id))
          .filter((n): n is GraphNode => Boolean(n))
          .filter((n) => !curFiltered || curMatched.has(n.id))
          .sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
          .slice(0, 14);

        for (const nb of neighbors) {
          renderNodePill(nb, "neighbor");
        }

        // Edge badges (shared tool / semantic %) if space allows
        for (const eb of edgeMidLabels.slice(0, 8)) {
          const pt = worldToScreen(eb.wx, eb.wy, cam);
          drawPill({
            cx: pt.x,
            cy: pt.y,
            text: truncateText(eb.text, 18),
            borderColor: hexToRgba(eb.color, 0.55),
            bgAlpha: 0.9,
            fontSize: 10,
            fontWeight: 500,
            textColor: eb.color,
            pad: 3,
          });
        }
      }

      // 6c. Priority 3: Cluster / Subcluster header pills
      const clustersOrdered = [...curClusters].sort((a, b) => {
        if (a.id === selCluster) return -1;
        if (b.id === selCluster) return 1;
        if (a.id === hovCluster) return -1;
        if (b.id === hovCluster) return 1;
        return b.count - a.count;
      });

      for (const c of clustersOrdered) {
        const isSel = selCluster === c.id;
        const isHov = hovCluster === c.id;
        const dimmed =
          (Boolean(egoId) && idx.nodeById.get(egoId!)?.cluster_id !== c.id) ||
          (Boolean(selCluster) &&
            !isSel &&
            !(selectedCategory && c.category === selectedCategory)) ||
          (hygieneActive && !idx.hygieneClusterIds.has(c.id));

        if (egoId && curIsolate && dimmed) continue;

        let anchorWy = c.y;
        if (lod >= 2 && c.hull && c.hull.length >= 3) {
          let hullMinY = Infinity;
          for (const [, hy] of c.hull) {
            if (hy < hullMinY) hullMinY = hy;
          }
          anchorWy = hullMinY - 10 / cam.k;
        } else if (lod === 1) {
          // Slightly above center on Macro so it rests cleanly over its territory
          anchorWy = c.y - Math.min(18, c.radius * 0.16);
        }

        const pt = worldToScreen(c.x, anchorWy, cam);
        const isSubcluster =
          Boolean(c.category) &&
          (idx.subclustersByCategory.get(c.category!)?.length ?? 0) > 1;
        const label =
          lod === 1
            ? formatMacroClusterLabel(c.label, c.category)
            : c.label;

        let rect: Rect | null = null;
        const offsetsY = lod === 1 ? [0, -20, 20, -34, 34] : [0, -16, 16];
        for (const dy of offsetsY) {
          rect = drawPill({
            cx: pt.x,
            cy: pt.y + dy,
            text: label,
            count: String(c.count),
            dotColor: c.color,
            borderColor: hexToRgba(
              isSel ? ACCENT_BRASS : c.color,
              isSel ? 0.85 : isHov ? 0.65 : dimmed ? 0.18 : 0.42
            ),
            bgAlpha: dimmed ? 0.62 : 0.9,
            fontSize: lod === 1 ? 12 : 12.5,
            fontWeight: 600,
            textColor: dimmed ? "rgba(238, 242, 237, 0.52)" : TEXT_INK,
            force: (isSel || isHov) && dy === 0,
            pad: lod === 1 ? 4 : 6,
          });
          if (rect) break;
        }

        if (rect) {
          clusterPillsHit.push({ id: c.id, rect });

          // At LOD 2 (Meso), show subcluster top-tools micro-caption for big categories (AI) & focused clusters
          if (
            lod === 2 &&
            !egoId &&
            (isSubcluster || isSel) &&
            c.top_tools.length > 0 &&
            !dimmed
          ) {
            const toolsLine = c.top_tools.slice(0, 3).join(" · ");
            drawPill({
              cx: pt.x,
              cy: rect.y1 + 4,
              text: truncateText(toolsLine, 34),
              borderColor: hexToRgba(c.color, 0.24),
              bgAlpha: 0.78,
              fontSize: 10,
              fontWeight: 500,
              textColor: TEXT_MUTED,
              alignTop: true,
              pad: 3,
            });
          }
        }
      }

      placedClusterPillsRef.current = clusterPillsHit;

      // 6c-bis. Macro client markers: compact brass diamond pills so clients stay unmistakable at LOD 1
      if (!egoId && lod === 1 && !hygieneActive && idx.clientNodes.length > 0) {
        let shownClients = 0;
        const maxMacroClients = 16;
        for (const cn of idx.clientNodes) {
          if (shownClients >= maxMacroClients) break;
          if (cn.id === selNode || cn.id === hovNode) continue;
          if (curFiltered && !curMatched.has(cn.id)) continue;
          if (selCluster) continue;

          const pt = worldToScreen(cn.x ?? 0, cn.y ?? 0, cam);
          if (pt.x < 28 || pt.x > width - 28 || pt.y < 56 || pt.y > height - 36) {
            continue;
          }

          const rScreen = baseNodeRadius(cn) * Math.pow(cam.k, 0.28) * 1.15;
          const offsetsY = [
            -(rScreen + 12),
            rScreen + 12,
            -(rScreen + 25),
            rScreen + 25,
          ];
          let rect: Rect | null = null;
          for (const dy of offsetsY) {
            rect = drawPill({
              cx: pt.x,
              cy: pt.y + dy,
              text: truncateText(cn.title, 20),
              dotColor: ACCENT_BRASS,
              dotShape: "diamond",
              borderColor: hexToRgba(ACCENT_BRASS, 0.72),
              bgAlpha: 0.9,
              fontSize: 10.5,
              fontWeight: 600,
              textColor: "#f3e3c6",
              pad: 3,
            });
            if (rect) break;
          }
          if (rect) {
            clientPillsHit.push({ id: cn.id, rect });
            shownClients++;
          }
        }
      }

      placedClientPillsRef.current = clientPillsHit;

      // 6d. Priority 4: Material / Client pills at LOD 2 (Meso) and LOD 3 (Micro)
      // Strictly capped (~22 at Meso, ~25 at Micro) with AABB non-overlap!
      if (!egoId && lod >= 2) {
        const maxAmbient = lod === 3 ? 25 : 22;
        let shown = 0;

        for (const n of idx.nodesByImportance) {
          if (shown >= maxAmbient) break;
          if (n.id === selNode || n.id === hovNode) continue;
          if (curFiltered && !curMatched.has(n.id)) continue;
          if (selCluster && n.cluster_id !== selCluster) continue;

          // At LOD 2, prioritize high-score materials & clients
          if (lod === 2 && !selCluster && n.type !== "client" && (n.score ?? 0) < 7) {
            continue;
          }

          const pt = worldToScreen(n.x ?? 0, n.y ?? 0, cam);
          if (pt.x < 32 || pt.x > width - 32 || pt.y < 64 || pt.y > height - 48) {
            continue;
          }

          if (renderNodePill(n, "ambient")) {
            shown++;
          }
        }
      }

      ctx.restore();
    }, [screenToWorld, worldToScreen]);

    drawRef.current = draw;

    // Hit-test helpers (nodes first, then cluster pills, then cluster hulls)
    const hitTest = useCallback(
      (sx: number, sy: number): { kind: "node"; node: GraphNode } | { kind: "cluster"; clusterId: string } | null => {
        const cam = camRef.current;
        const {
          nodes: curNodes,
          clusters: curClusters,
          matchedNodeIds: curMatched,
          hasActiveFilter: curFiltered,
        } = stateRef.current;

        // 1. Check nodes in screen space
        let bestNode: GraphNode | null = null;
        let bestDist = Infinity;
        for (const n of curNodes) {
          if (curFiltered && !curMatched.has(n.id)) continue;
          const pt = worldToScreen(n.x ?? 0, n.y ?? 0, cam);
          const d = Math.hypot(sx - pt.x, sy - pt.y);
          const hitR = Math.max(10, baseNodeRadius(n) * Math.pow(cam.k, 0.28) + 5);
          if (d <= hitR && d < bestDist) {
            bestDist = d;
            bestNode = n;
          }
        }
        if (bestNode) return { kind: "node", node: bestNode };

        // 1b. Check placed client pill rectangles (Macro client markers)
        for (const p of placedClientPillsRef.current) {
          if (
            sx >= p.rect.x0 - 2 &&
            sx <= p.rect.x1 + 2 &&
            sy >= p.rect.y0 - 2 &&
            sy <= p.rect.y1 + 2
          ) {
            const clientNode = curNodes.find((n) => n.id === p.id);
            if (clientNode) return { kind: "node", node: clientNode };
          }
        }

        // 2. Check placed cluster pill rectangles
        for (const p of placedClusterPillsRef.current) {
          if (
            sx >= p.rect.x0 - 2 &&
            sx <= p.rect.x1 + 2 &&
            sy >= p.rect.y0 - 2 &&
            sy <= p.rect.y1 + 2
          ) {
            return { kind: "cluster", clusterId: p.id };
          }
        }

        // 3. Check cluster hulls in world space (smallest cluster wins on edge overlaps)
        const w = screenToWorld(sx, sy, cam);
        let bestCluster: GraphCluster | null = null;
        for (const c of curClusters) {
          const hit =
            c.hull && c.hull.length >= 3
              ? pointInPolygon(w.x, w.y, c.hull)
              : Math.hypot(w.x - c.x, w.y - c.y) <= c.radius;
          if (hit) {
            if (!bestCluster || c.radius < bestCluster.radius) {
              bestCluster = c;
            }
          }
        }
        if (bestCluster) return { kind: "cluster", clusterId: bestCluster.id };

        return null;
      },
      [screenToWorld, worldToScreen]
    );

    // ResizeObserver for crisp DPR sizing + initial instant fit (no physics explosion!)
    useEffect(() => {
      const container = containerRef.current;
      const canvas = canvasRef.current;
      if (!container || !canvas) return;

      const updateSize = () => {
        const rect = container.getBoundingClientRect();
        const width = Math.max(1, Math.floor(rect.width));
        const height = Math.max(1, Math.floor(rect.height));
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        sizeRef.current = { width, height, dpr };
        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;

        if (!fittedOnceRef.current && stateRef.current.nodes.length > 0) {
          fittedOnceRef.current = true;
          camRef.current = computeOverviewCamera();
        }
        scheduleDraw();
      };

      updateSize();
      const ro = new ResizeObserver(updateSize);
      ro.observe(container);
      return () => ro.disconnect();
    }, [computeOverviewCamera, scheduleDraw]);

    // Fit instantaneously on first data arrival
    useEffect(() => {
      if (nodes.length > 0 && !fittedOnceRef.current && sizeRef.current.width > 10) {
        fittedOnceRef.current = true;
        camRef.current = computeOverviewCamera();
      }
      scheduleDraw();
    }, [
      clusters,
      nodes,
      edges,
      clusterEdges,
      matchedNodeIds,
      hasActiveFilter,
      viewMode,
      selectedClusterId,
      selectedNodeId,
      hoveredNodeId,
      hoveredClusterId,
      isolateEgo,
      inspectorOpen,
      computeOverviewCamera,
      scheduleDraw,
    ]);

    // Non-passive wheel listener for smooth zoom around cursor
    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      const onWheel = (e: WheelEvent) => {
        e.preventDefault();
        animRef.current = null;
        const rect = canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left;
        const sy = e.clientY - rect.top;

        const before = screenToWorld(sx, sy, camRef.current);
        const factor = Math.exp(-e.deltaY * 0.0014);
        const nextK = Math.max(
          overviewScaleRef.current * 0.55,
          Math.min(5.0, camRef.current.k * factor)
        );
        const { width, height } = sizeRef.current;
        camRef.current = {
          k: nextK,
          x: before.x - (sx - width / 2) / nextK,
          y: before.y - (sy - height / 2) / nextK,
        };
        scheduleDraw();
      };

      canvas.addEventListener("wheel", onWheel, { passive: false });
      return () => canvas.removeEventListener("wheel", onWheel);
    }, [screenToWorld, scheduleDraw]);

    // Pointer events (pan, pinch zoom, hover, click, double-click)
    const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.setPointerCapture(e.pointerId);
      animRef.current = null;

      const rect = canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const p = pointerRef.current;
      p.pointers.set(e.pointerId, { x: sx, y: sy });

      if (p.pointers.size === 1) {
        p.active = true;
        p.dragging = false;
        p.startX = sx;
        p.startY = sy;
        p.camStartX = camRef.current.x;
        p.camStartY = camRef.current.y;
      } else if (p.pointers.size === 2) {
        const pts = [...p.pointers.values()];
        p.pinchDist = Math.hypot(pts[1]!.x - pts[0]!.x, pts[1]!.y - pts[0]!.y);
        p.pinchCamK = camRef.current.k;
        p.dragging = true;
      }
    };

    const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const p = pointerRef.current;

      if (p.pointers.has(e.pointerId)) {
        p.pointers.set(e.pointerId, { x: sx, y: sy });
      }

      if (p.pointers.size === 2 && p.pinchDist > 8) {
        const pts = [...p.pointers.values()];
        const dist = Math.hypot(pts[1]!.x - pts[0]!.x, pts[1]!.y - pts[0]!.y);
        const mx = (pts[0]!.x + pts[1]!.x) / 2;
        const my = (pts[0]!.y + pts[1]!.y) / 2;
        const before = screenToWorld(mx, my, camRef.current);
        const nextK = Math.max(
          overviewScaleRef.current * 0.55,
          Math.min(5.0, p.pinchCamK * (dist / p.pinchDist))
        );
        const { width, height } = sizeRef.current;
        camRef.current = {
          k: nextK,
          x: before.x - (mx - width / 2) / nextK,
          y: before.y - (my - height / 2) / nextK,
        };
        scheduleDraw();
        return;
      }

      if (p.active) {
        const dx = sx - p.startX;
        const dy = sy - p.startY;
        if (Math.hypot(dx, dy) > 4) {
          p.dragging = true;
        }
        if (p.dragging) {
          camRef.current = {
            ...camRef.current,
            x: p.camStartX - dx / camRef.current.k,
            y: p.camStartY - dy / camRef.current.k,
          };
          canvas.style.cursor = "grabbing";
          scheduleDraw();
          return;
        }
      }

      // Hover hit-test (mouse only)
      if (e.pointerType !== "touch") {
        const hit = hitTest(sx, sy);
        if (hit?.kind === "node") {
          canvas.style.cursor = "pointer";
          if (stateRef.current.hoveredNodeId !== hit.node.id) {
            onHoverNode(hit.node.id);
          }
          if (stateRef.current.hoveredClusterId !== null) {
            onHoverCluster(null);
          }
        } else if (hit?.kind === "cluster") {
          canvas.style.cursor = "pointer";
          if (stateRef.current.hoveredNodeId !== null) {
            onHoverNode(null);
          }
          if (stateRef.current.hoveredClusterId !== hit.clusterId) {
            onHoverCluster(hit.clusterId);
          }
        } else {
          canvas.style.cursor = "grab";
          if (stateRef.current.hoveredNodeId !== null) onHoverNode(null);
          if (stateRef.current.hoveredClusterId !== null) onHoverCluster(null);
        }
      }
    };

    const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      const p = pointerRef.current;
      p.pointers.delete(e.pointerId);

      if (canvas && canvas.hasPointerCapture(e.pointerId)) {
        canvas.releasePointerCapture(e.pointerId);
      }

      if (p.active && !p.dragging && canvas) {
        const rect = canvas.getBoundingClientRect();
        const sx = e.clientX - rect.left;
        const sy = e.clientY - rect.top;
        const hit = hitTest(sx, sy);

        if (hit?.kind === "node") {
          const now = performance.now();
          if (
            p.lastTapNodeId === hit.node.id &&
            now - p.lastTapTime < 340
          ) {
            p.lastTapNodeId = null;
            onOpenNode(hit.node);
          } else {
            p.lastTapNodeId = hit.node.id;
            p.lastTapTime = now;
            onSelectNode(hit.node.id);
          }
        } else if (hit?.kind === "cluster") {
          p.lastTapNodeId = null;
          onSelectCluster(hit.clusterId);
        } else {
          p.lastTapNodeId = null;
          onClearSelection();
        }
      }

      if (p.pointers.size === 0) {
        p.active = false;
        p.dragging = false;
        if (canvas) canvas.style.cursor = "grab";
      }
    };

    const onPointerLeave = () => {
      if (stateRef.current.hoveredNodeId !== null) onHoverNode(null);
      if (stateRef.current.hoveredClusterId !== null) onHoverCluster(null);
    };

    useEffect(() => {
      return () => {
        if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      };
    }, []);

    return (
      <div
        ref={containerRef}
        className="graph-stage relative h-full w-full overflow-hidden"
        style={{ background: BG_BASE }}
      >
        <canvas
          ref={canvasRef}
          className="block h-full w-full"
          style={{ cursor: "grab" }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={onPointerLeave}
        />
      </div>
    );
  }
);

export default KnowledgeAtlasCanvas;
