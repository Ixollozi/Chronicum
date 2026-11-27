"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FancySelect } from "@/components/FancySelect";
import KnowledgeAtlasCanvas, {
  EDGE_COLORS,
  isUncategorizedMaterial,
  type AtlasLodLevel,
  type AtlasViewMode,
  type KnowledgeAtlasHandle,
} from "@/components/KnowledgeAtlasCanvas";
import {
  client,
  type GraphCluster,
  type GraphEdge,
  type GraphNode,
  type GraphPayload,
} from "@/lib/api";
import {
  nodeTypeLabel,
  searchGraphClusters,
  searchGraphNodes,
} from "@/lib/graphSearch";

function ruCount(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return `${n} ${one}`;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return `${n} ${few}`;
  return `${n} ${many}`;
}

function sourceLabel(src: string): string {
  const s = (src || "").toLowerCase();
  if (s === "instagram") return "Instagram";
  if (s === "youtube") return "YouTube";
  if (s === "pdf") return "PDF";
  if (s === "article") return "Статья";
  if (s === "note") return "Заметка";
  return src || "Материал";
}

function formatDate(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function lodLabel(lod: AtlasLodLevel): string {
  if (lod === 1) return "Макро";
  if (lod === 2) return "Мезо";
  return "Микро";
}

function shortSubclusterName(label: string, category: string | null): string {
  if (category && label.startsWith(`${category} · `)) {
    return label.slice(category.length + 3);
  }
  return label;
}

type RelatedNeighbor = {
  node: GraphNode;
  reason: string;
  badge: string | null;
  similarity?: number | null;
};

export default function GraphPage() {
  const router = useRouter();
  const atlasRef = useRef<KnowledgeAtlasHandle>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchWrapRef = useRef<HTMLDivElement>(null);
  const filtersWrapRef = useRef<HTMLDivElement>(null);
  const focusAppliedRef = useRef<string | null>(null);

  const [raw, setRaw] = useState<GraphPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Selection & hover state
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [hoveredClusterId, setHoveredClusterId] = useState<string | null>(null);
  const [isolateEgo, setIsolateEgo] = useState(false);

  // HUD & view controls
  const [viewMode, setViewMode] = useState<AtlasViewMode>("clusters");
  const [lod, setLod] = useState<AtlasLodLevel>(1);
  const [legendOpen, setLegendOpen] = useState(false);
  const [clusterSort, setClusterSort] = useState<"score" | "recent">("score");

  // Search & Facet filters
  const [q, setQ] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [toolFilter, setToolFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [minScore, setMinScore] = useState(0);

  const load = useCallback(async () => {
    try {
      const payload = await client.graph();
      setRaw(payload);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить атлас");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const clusters = useMemo<GraphCluster[]>(() => raw?.clusters ?? [], [raw]);
  const nodes = useMemo<GraphNode[]>(() => raw?.nodes ?? [], [raw]);
  const edges = useMemo<GraphEdge[]>(
    () => raw?.edges ?? raw?.links ?? [],
    [raw]
  );
  const clusterEdges = useMemo(() => raw?.cluster_edges ?? [], [raw]);
  const meta = raw?.meta ?? null;

  const clusterById = useMemo(() => {
    const m = new Map<string, GraphCluster>();
    for (const c of clusters) m.set(c.id, c);
    return m;
  }, [clusters]);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of nodes) m.set(n.id, n);
    return m;
  }, [nodes]);

  // Facet options from backend meta.facets
  const categoryOptions = useMemo(() => {
    const items = meta?.facets?.categories ?? [];
    return [
      { value: "", label: "Все категории" },
      ...items.map((f) => ({ value: f.key, label: `${f.label} (${f.count})` })),
    ];
  }, [meta]);

  const toolOptions = useMemo(() => {
    const items = meta?.facets?.tools ?? [];
    return [
      { value: "", label: "Все инструменты" },
      ...items.map((f) => ({ value: f.key, label: `${f.label} (${f.count})` })),
    ];
  }, [meta]);

  const tagOptions = useMemo(() => {
    const items = meta?.facets?.tags ?? [];
    return [
      { value: "", label: "Все теги" },
      ...items.map((f) => ({ value: f.key, label: `${f.label} (${f.count})` })),
    ];
  }, [meta]);

  const sourceOptions = useMemo(() => {
    const items = meta?.facets?.source_types ?? [];
    return [
      { value: "", label: "Все источники" },
      ...items.map((f) => ({
        value: f.key,
        label: `${sourceLabel(f.label)} (${f.count})`,
      })),
    ];
  }, [meta]);

  const activeFilterCount =
    (categoryFilter ? 1 : 0) +
    (toolFilter ? 1 : 0) +
    (tagFilter ? 1 : 0) +
    (sourceFilter ? 1 : 0) +
    (minScore > 0 ? 1 : 0);

  const hasActiveFilter = activeFilterCount > 0;

  const matchedNodeIds = useMemo(() => {
    const out = new Set<string>();
    const catKey = categoryFilter.toLowerCase();
    const toolKey = toolFilter.toLowerCase();
    const tagKey = tagFilter.toLowerCase();
    const srcKey = sourceFilter.toLowerCase();

    for (const n of nodes) {
      if (catKey) {
        if (catKey === "__uncategorized__") {
          if (n.category && n.cluster_id !== "cluster:uncategorized") continue;
        } else if ((n.category || "").toLowerCase() !== catKey) {
          continue;
        }
      }
      if (toolKey) {
        const hasTool = (n.tools || []).some(
          (t) => t.toLowerCase() === toolKey
        );
        if (!hasTool) continue;
      }
      if (tagKey) {
        const hasTag = (n.tags || []).some((t) => t.toLowerCase() === tagKey);
        if (!hasTag) continue;
      }
      if (srcKey) {
        if ((n.source_type || "").toLowerCase() !== srcKey) continue;
      }
      if (minScore > 0) {
        if (n.type !== "client" && (n.score ?? -1) < minScore) continue;
      }
      out.add(n.id);
    }
    return out;
  }, [nodes, categoryFilter, toolFilter, tagFilter, sourceFilter, minScore]);

  // Search dropdown hits (clusters + nodes)
  const searchClusterHits = useMemo(
    () => (q.trim().length >= 1 ? searchGraphClusters(clusters, q, 4) : []),
    [clusters, q]
  );
  const searchNodeHits = useMemo(
    () => (q.trim().length >= 1 ? searchGraphNodes(nodes, q, 8) : []),
    [nodes, q]
  );

  // Handlers
  const handleSelectCluster = useCallback((clusterId: string) => {
    setSelectedNodeId(null);
    setIsolateEgo(false);
    setSelectedClusterId(clusterId);
    setSearchOpen(false);
    setFiltersOpen(false);
    requestAnimationFrame(() => {
      atlasRef.current?.focusCluster(clusterId);
    });
  }, []);

  const handleSelectNode = useCallback(
    (nodeId: string, opts?: { focus?: boolean }) => {
      const node = nodeById.get(nodeId);
      setSelectedNodeId(nodeId);
      setIsolateEgo(false);
      if (node?.cluster_id) {
        setSelectedClusterId(node.cluster_id);
      }
      setSearchOpen(false);
      setFiltersOpen(false);
      if (opts?.focus !== false) {
        requestAnimationFrame(() => {
          atlasRef.current?.focusNode(nodeId);
        });
      }
    },
    [nodeById]
  );

  const handleOpenNode = useCallback(
    (node: GraphNode) => {
      if (node.type === "client") {
        router.push(`/clients/${node.id}`);
      } else {
        router.push(`/m/${node.id}`);
      }
    },
    [router]
  );

  const handleOverview = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedClusterId(null);
    setIsolateEgo(false);
    setSearchOpen(false);
    setFiltersOpen(false);
    requestAnimationFrame(() => {
      atlasRef.current?.fitOverview(false);
    });
  }, []);

  const handleClearSelection = useCallback(() => {
    if (selectedNodeId) {
      setSelectedNodeId(null);
      setIsolateEgo(false);
    } else if (selectedClusterId) {
      setSelectedClusterId(null);
    }
    setSearchOpen(false);
    setFiltersOpen(false);
  }, [selectedNodeId, selectedClusterId]);

  const clearFilters = useCallback(() => {
    setCategoryFilter("");
    setToolFilter("");
    setTagFilter("");
    setSourceFilter("");
    setMinScore(0);
  }, []);

  // Deep link /graph?focus=<id>
  useEffect(() => {
    if (!raw || typeof window === "undefined") return;
    const focus = new URLSearchParams(window.location.search).get("focus");
    if (!focus || focusAppliedRef.current === focus) return;
    if (nodeById.has(focus)) {
      focusAppliedRef.current = focus;
      handleSelectNode(focus, { focus: true });
    } else if (clusterById.has(focus)) {
      focusAppliedRef.current = focus;
      handleSelectCluster(focus);
    }
  }, [raw, nodeById, clusterById, handleSelectNode, handleSelectCluster]);

  // Close popovers on outside click
  useEffect(() => {
    function onDocDown(e: MouseEvent) {
      const target = e.target as Node;
      if (searchOpen && !searchWrapRef.current?.contains(target)) {
        setSearchOpen(false);
      }
      if (
        filtersOpen &&
        !filtersWrapRef.current?.contains(target) &&
        !(target as Element | null)?.closest?.(".fancy-select-menu")
      ) {
        setFiltersOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [searchOpen, filtersOpen]);

  // Keyboard shortcuts: / search, Esc step-back, F overview, +/- zoom
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const inInput =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      if (e.key === "/" && !inInput) {
        e.preventDefault();
        searchInputRef.current?.focus();
        setSearchOpen(true);
        return;
      }
      if (e.key === "Escape") {
        if (searchOpen) {
          setSearchOpen(false);
          searchInputRef.current?.blur();
        } else if (filtersOpen) {
          setFiltersOpen(false);
        } else if (selectedNodeId) {
          setSelectedNodeId(null);
          setIsolateEgo(false);
        } else if (selectedClusterId) {
          setSelectedClusterId(null);
          atlasRef.current?.fitOverview(false);
        }
        return;
      }
      if (inInput) return;

      if (e.key === "f" || e.key === "F" || e.key === "а" || e.key === "А") {
        e.preventDefault();
        handleOverview();
      } else if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        atlasRef.current?.zoomBy(1.3);
      } else if (e.key === "-") {
        e.preventDefault();
        atlasRef.current?.zoomBy(1 / 1.3);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [searchOpen, filtersOpen, selectedNodeId, selectedClusterId, handleOverview]);

  // Derived Inspector Data
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) ?? null : null;
  const selectedCluster = selectedClusterId
    ? clusterById.get(selectedClusterId) ?? null
    : null;
  const inspectorOpen = Boolean(selectedNode || selectedCluster);

  const siblingSubclusters = useMemo(() => {
    if (!selectedCluster?.category) return [];
    const list = clusters.filter(
      (c) => c.category === selectedCluster.category
    );
    return list.length > 1
      ? [...list].sort((a, b) => b.count - a.count)
      : [];
  }, [clusters, selectedCluster]);

  const clusterMaterials = useMemo(() => {
    if (!selectedCluster) return [];
    const list = nodes.filter(
      (n) =>
        n.cluster_id === selectedCluster.id &&
        (!hasActiveFilter || matchedNodeIds.has(n.id))
    );
    if (clusterSort === "recent") {
      return [...list].sort((a, b) =>
        (b.created_at || "").localeCompare(a.created_at || "")
      );
    }
    return [...list].sort(
      (a, b) =>
        (b.score ?? -1) - (a.score ?? -1) ||
        b.degree - a.degree ||
        a.title.localeCompare(b.title, "ru")
    );
  }, [nodes, selectedCluster, hasActiveFilter, matchedNodeIds, clusterSort]);

  const groupedNeighbors = useMemo(() => {
    if (!selectedNode) return null;
    const groups: {
      shared_tool: RelatedNeighbor[];
      semantic: RelatedNeighbor[];
      client: RelatedNeighbor[];
      link: RelatedNeighbor[];
    } = {
      shared_tool: [],
      semantic: [],
      client: [],
      link: [],
    };
    const seen = new Set<string>();

    for (const e of edges) {
      let otherId: string | null = null;
      if (e.source === selectedNode.id) otherId = e.target;
      else if (e.target === selectedNode.id) otherId = e.source;
      if (!otherId) continue;
      const other = nodeById.get(otherId);
      if (!other) continue;

      const dedupKey = `${e.reason}:${otherId}`;
      if (seen.has(dedupKey)) continue;
      seen.add(dedupKey);

      const item: RelatedNeighbor = {
        node: other,
        reason: e.reason,
        similarity: e.similarity,
        badge:
          e.reason === "shared_tool"
            ? e.label || "Инструмент"
            : e.reason === "semantic" && typeof e.similarity === "number"
              ? `${Math.round(e.similarity * 100)}%`
              : e.reason === "client"
                ? "Клиент"
                : e.label || "Ссылка",
      };

      if (e.reason === "shared_tool") groups.shared_tool.push(item);
      else if (e.reason === "semantic") groups.semantic.push(item);
      else if (e.reason === "client") groups.client.push(item);
      else groups.link.push(item);
    }

    groups.shared_tool.sort(
      (a, b) => (b.node.score ?? -1) - (a.node.score ?? -1)
    );
    groups.semantic.sort(
      (a, b) => (b.similarity ?? 0) - (a.similarity ?? 0)
    );
    groups.client.sort((a, b) =>
      a.node.title.localeCompare(b.node.title, "ru")
    );
    groups.link.sort((a, b) => (b.node.score ?? -1) - (a.node.score ?? -1));

    return groups;
  }, [selectedNode, edges, nodeById]);

  const toggleIsolateEgo = () => {
    if (!selectedNode) return;
    const next = !isolateEgo;
    setIsolateEgo(next);
    requestAnimationFrame(() => {
      atlasRef.current?.focusNode(selectedNode.id, { isolate: next });
    });
  };

  const hoveredNodeObj = hoveredNodeId ? nodeById.get(hoveredNodeId) ?? null : null;
  const hygieneCount = useMemo(
    () =>
      nodes.reduce(
        (acc, n) => acc + (isUncategorizedMaterial(n) ? 1 : 0),
        0
      ),
    [nodes]
  );

  return (
    <div className="relative h-[100dvh] w-full overflow-hidden bg-[#0b0e0d] text-[var(--ink)]">
      {/* Main deterministic Knowledge Atlas Canvas */}
      <KnowledgeAtlasCanvas
        ref={atlasRef}
        clusters={clusters}
        nodes={nodes}
        edges={edges}
        clusterEdges={clusterEdges}
        matchedNodeIds={matchedNodeIds}
        hasActiveFilter={hasActiveFilter}
        viewMode={viewMode}
        selectedClusterId={selectedClusterId}
        selectedNodeId={selectedNodeId}
        hoveredNodeId={hoveredNodeId}
        hoveredClusterId={hoveredClusterId}
        isolateEgo={isolateEgo}
        inspectorOpen={inspectorOpen}
        onHoverNode={setHoveredNodeId}
        onHoverCluster={setHoveredClusterId}
        onSelectNode={(id) => handleSelectNode(id)}
        onOpenNode={handleOpenNode}
        onSelectCluster={handleSelectCluster}
        onClearSelection={handleClearSelection}
        onZoomStateChange={({ lod: nextLod }) => setLod(nextLod)}
      />

      {/* Loading / Error / Empty states */}
      {!raw && !error && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="atlas-glass rounded-full px-5 py-2.5 text-sm text-[var(--muted)]">
            Загружаю атлас знаний…
          </div>
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex items-center justify-center p-4">
          <div className="atlas-glass max-w-md rounded-2xl p-5 text-center">
            <p className="text-sm text-[var(--warn)]">{error}</p>
            <button
              type="button"
              onClick={load}
              className="mt-3 rounded-full bg-[var(--accent)] px-4 py-1.5 text-xs font-medium text-[var(--btn-fg)]"
            >
              Повторить
            </button>
          </div>
        </div>
      )}

      {/* Top-Left Compact HUD: Search + Facet Filters + View Mode */}
      <div className="pointer-events-none absolute top-3 left-3 right-3 z-20 flex flex-col items-start gap-2 md:top-3.5 md:left-4 md:right-auto">
        <div className="pointer-events-auto flex w-full max-w-[460px] flex-wrap items-center gap-1.5 sm:flex-nowrap">
          {/* Search input with live dropdown */}
          <div ref={searchWrapRef} className="relative min-w-0 flex-1">
            <svg
              className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--muted)]"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
            <input
              ref={searchInputRef}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setSearchOpen(true);
              }}
              onFocus={() => setSearchOpen(true)}
              placeholder="Поиск по атласу… ( / )"
              aria-label="Поиск по атласу знаний"
              className="atlas-glass h-9 w-full rounded-full pl-8 pr-8 text-[13px] text-[var(--ink)] placeholder:text-[var(--muted)] focus:border-[var(--accent)] focus:outline-none"
            />
            {q && (
              <button
                type="button"
                onClick={() => {
                  setQ("");
                  searchInputRef.current?.focus();
                }}
                aria-label="Очистить поиск"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-[var(--muted)] hover:text-[var(--ink)]"
              >
                ×
              </button>
            )}

            {searchOpen &&
              q.trim().length > 0 &&
              (searchClusterHits.length > 0 || searchNodeHits.length > 0) && (
                <div className="atlas-glass absolute left-0 right-0 top-[calc(100%+6px)] z-40 max-h-[60vh] overflow-y-auto rounded-2xl p-1.5 shadow-2xl">
                  {searchClusterHits.length > 0 && (
                    <div className="mb-1">
                      <div className="px-2.5 py-1 text-[10px] uppercase tracking-[0.14em] text-[var(--muted)]">
                        Кластеры
                      </div>
                      {searchClusterHits.map((c) => (
                        <button
                          key={c.id}
                          type="button"
                          onClick={() => handleSelectCluster(c.id)}
                          className="flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left text-[13px] transition hover:bg-white/[0.06]"
                        >
                          <span className="flex min-w-0 items-center gap-2">
                            <span
                              className="h-2.5 w-2.5 shrink-0 rounded-full"
                              style={{ backgroundColor: c.color }}
                            />
                            <span className="truncate text-[var(--ink)]">
                              {c.label}
                            </span>
                          </span>
                          <span className="shrink-0 text-[11px] text-[var(--muted)]">
                            {c.count}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}

                  {searchNodeHits.length > 0 && (
                    <div>
                      <div className="px-2.5 py-1 text-[10px] uppercase tracking-[0.14em] text-[var(--muted)]">
                        Материалы и клиенты
                      </div>
                      {searchNodeHits.map((n) => {
                        const c = n.cluster_id
                          ? clusterById.get(n.cluster_id)
                          : null;
                        return (
                          <button
                            key={n.id}
                            type="button"
                            onClick={() => handleSelectNode(n.id)}
                            className="flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left text-[13px] transition hover:bg-white/[0.06]"
                          >
                            <span className="flex min-w-0 items-center gap-2">
                              {n.type === "client" ? (
                                <span
                                  className="h-2 w-2 shrink-0 rotate-45 bg-[var(--accent)]"
                                  aria-hidden
                                />
                              ) : (
                                <span
                                  className="h-2 w-2 shrink-0 rounded-full"
                                  style={{
                                    backgroundColor: c?.color ?? "#6c7570",
                                  }}
                                />
                              )}
                              <span className="truncate text-[var(--ink)]">
                                {n.title}
                              </span>
                            </span>
                            <span className="shrink-0 text-[11px] text-[var(--muted)]">
                              {n.type === "client"
                                ? nodeTypeLabel(n.type)
                                : n.score != null
                                  ? `${n.score}/10`
                                  : n.category || ""}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
          </div>

          {/* Filters button + popover */}
          <div ref={filtersWrapRef} className="relative shrink-0">
            <button
              type="button"
              onClick={() => {
                setFiltersOpen((v) => !v);
                setSearchOpen(false);
              }}
              aria-expanded={filtersOpen}
              className={`atlas-glass flex h-9 items-center gap-1.5 rounded-full px-3.5 text-[12.5px] font-medium transition ${
                filtersOpen || activeFilterCount > 0
                  ? "border-[var(--accent)]/60 text-[var(--accent)]"
                  : "text-[var(--ink)] hover:border-white/25"
              }`}
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M3 6h18M7 12h10M10 18h4" />
              </svg>
              <span>Фильтры</span>
              {activeFilterCount > 0 && (
                <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--accent)] px-1 text-[10px] font-semibold text-[var(--btn-fg)]">
                  {activeFilterCount}
                </span>
              )}
            </button>

            {filtersOpen && (
              <div className="atlas-glass absolute left-0 top-[calc(100%+8px)] z-40 w-[min(92vw,340px)] rounded-2xl p-3.5 shadow-2xl md:left-auto md:right-0">
                <div className="mb-2.5 flex items-center justify-between">
                  <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--muted)]">
                    Фасеты атласа
                  </span>
                  {activeFilterCount > 0 && (
                    <button
                      type="button"
                      onClick={clearFilters}
                      className="text-[11.5px] text-[var(--accent)] hover:underline"
                    >
                      Сбросить
                    </button>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-2.5">
                  <div className="graph-filter-field">
                    <span className="graph-filter-label">Категория</span>
                    <FancySelect
                      value={categoryFilter}
                      onChange={setCategoryFilter}
                      options={categoryOptions}
                      aria-label="Категория"
                      variant="graph"
                    />
                  </div>
                  <div className="graph-filter-field">
                    <span className="graph-filter-label">Инструмент</span>
                    <FancySelect
                      value={toolFilter}
                      onChange={setToolFilter}
                      options={toolOptions}
                      aria-label="Инструмент"
                      variant="graph"
                    />
                  </div>
                  <div className="graph-filter-field">
                    <span className="graph-filter-label">Тег</span>
                    <FancySelect
                      value={tagFilter}
                      onChange={setTagFilter}
                      options={tagOptions}
                      aria-label="Тег"
                      variant="graph"
                    />
                  </div>
                  <div className="graph-filter-field">
                    <span className="graph-filter-label">Источник</span>
                    <FancySelect
                      value={sourceFilter}
                      onChange={setSourceFilter}
                      options={sourceOptions}
                      aria-label="Источник"
                      variant="graph"
                    />
                  </div>

                  <div className="graph-filter-field graph-filter-field--score">
                    <span className="graph-filter-label">Оценка (Score)</span>
                    <div className="graph-score-seg">
                      {[
                        { v: 0, label: "Все" },
                        { v: 5, label: "≥ 5" },
                        { v: 7, label: "≥ 7" },
                        { v: 8, label: "≥ 8" },
                      ].map((opt) => (
                        <button
                          key={opt.v}
                          type="button"
                          data-active={minScore === opt.v}
                          onClick={() => setMinScore(opt.v)}
                          className="graph-score-seg-item"
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Compact Mode Switcher + Live Counter + LOD Badge */}
        <div className="pointer-events-auto flex flex-wrap items-center gap-1.5">
          <div
            className="atlas-glass inline-flex items-center rounded-full p-0.5 text-[11.5px]"
            role="tablist"
            aria-label="Режим карты"
          >
            {(
              [
                { id: "clusters", label: "Кластеры" },
                { id: "links", label: "Связи" },
                {
                  id: "hygiene",
                  label:
                    hygieneCount > 0
                      ? `Неразобранное (${hygieneCount})`
                      : "Неразобранное",
                },
              ] as { id: AtlasViewMode; label: string }[]
            ).map((tab) => {
              const active = viewMode === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setViewMode(tab.id)}
                  className={`rounded-full px-2.5 py-1 transition ${
                    active
                      ? "bg-[var(--accent)]/20 font-medium text-[var(--accent)]"
                      : "text-[var(--muted)] hover:text-[var(--ink)]"
                  }`}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>

          {viewMode === "hygiene" && raw && hygieneCount === 0 && (
            <div className="atlas-glass inline-flex items-center gap-1.5 rounded-full border border-[var(--accent)]/30 px-2.5 py-1 text-[11px] text-[var(--ink)]/85">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
              <span>Нет материалов без категории</span>
            </div>
          )}

          {raw && (
            <div className="atlas-glass flex items-center gap-2 rounded-full px-3 py-1 text-[11.5px] text-[var(--muted)]">
              <span>
                {hasActiveFilter
                  ? `${matchedNodeIds.size} из ${nodes.length}`
                  : ruCount(
                      meta?.total_materials ?? nodes.length,
                      "материал",
                      "материала",
                      "материалов"
                    )}
                {" · "}
                {ruCount(
                  meta?.total_clusters ?? clusters.length,
                  "кластер",
                  "кластера",
                  "кластеров"
                )}
              </span>
              <span className="h-3 w-px bg-white/15" aria-hidden />
              <span className="text-[var(--accent)]">{lodLabel(lod)}</span>
            </div>
          )}

          {hasActiveFilter && (
            <button
              type="button"
              onClick={clearFilters}
              className="atlas-glass rounded-full px-2.5 py-1 text-[11px] text-[var(--accent)] hover:border-[var(--accent)]/60"
            >
              Сбросить фильтры ×
            </button>
          )}
        </div>
      </div>

      {/* Bottom-Left Controls: Zoom (+/-), Overview (F), and Collapsible Legend */}
      <div className="pointer-events-none absolute bottom-[calc(var(--app-tabbar-h)+0.75rem)] left-3 z-20 flex flex-col items-start gap-2 md:bottom-4 md:left-4">
        {legendOpen && (
          <div className="atlas-glass pointer-events-auto w-[275px] rounded-2xl p-3 text-[11.5px] text-[var(--muted)] shadow-xl">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-[var(--ink)]">
                Легенда атласа
              </span>
              <button
                type="button"
                onClick={() => setLegendOpen(false)}
                className="text-sm leading-none text-[var(--muted)] hover:text-[var(--ink)]"
                aria-label="Скрыть легенду"
              >
                ×
              </button>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1">
                  <span className="h-3 w-3 rounded-full border border-white/50 bg-[#7ba892]" />
                  <span className="h-2 w-2 rounded-full bg-[#7ba892]" />
                  <span className="h-1.5 w-1.5 rounded-full bg-[#7ba892]/75" />
                </span>
                <span>Размер точки = оценка (≥8, 5–7, &lt;5)</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="ml-1 h-2.5 w-2.5 rotate-45 border border-[#f4e6cc] bg-[var(--accent)]" />
                <span>Ромб — клиент</span>
              </div>
              <div className="pt-1 text-[10px] uppercase tracking-[0.12em] text-[var(--muted)]/80">
                1-hop связи при наведении / клике
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                <span className="flex items-center gap-1.5">
                  <span
                    className="h-0.5 w-4 rounded"
                    style={{ backgroundColor: EDGE_COLORS.shared_tool }}
                  />
                  Инструмент
                </span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="h-0.5 w-4 rounded"
                    style={{ backgroundColor: EDGE_COLORS.semantic }}
                  />
                  По смыслу
                </span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="h-0.5 w-4 rounded"
                    style={{ backgroundColor: EDGE_COLORS.client }}
                  />
                  Клиент
                </span>
                <span className="flex items-center gap-1.5">
                  <span
                    className="h-0.5 w-4 rounded"
                    style={{ backgroundColor: EDGE_COLORS.link }}
                  />
                  Ссылка
                </span>
              </div>
            </div>
          </div>
        )}

        <div className="pointer-events-auto flex items-center gap-1.5">
          <div className="atlas-glass flex items-center overflow-hidden rounded-full">
            <button
              type="button"
              onClick={() => atlasRef.current?.zoomBy(1 / 1.32)}
              aria-label="Уменьшить масштаб"
              title="Отдалить ( - )"
              className="flex h-8 w-8 items-center justify-center text-sm text-[var(--ink)] transition hover:bg-white/[0.07]"
            >
              −
            </button>
            <span className="h-4 w-px bg-white/10" aria-hidden />
            <button
              type="button"
              onClick={() => atlasRef.current?.zoomBy(1.32)}
              aria-label="Увеличить масштаб"
              title="Приблизить ( + )"
              className="flex h-8 w-8 items-center justify-center text-sm text-[var(--ink)] transition hover:bg-white/[0.07]"
            >
              +
            </button>
            <span className="h-4 w-px bg-white/10" aria-hidden />
            <button
              type="button"
              onClick={handleOverview}
              title="Показать весь атлас ( F )"
              className="h-8 px-3 text-[12px] font-medium text-[var(--ink)] transition hover:bg-white/[0.07] hover:text-[var(--accent)]"
            >
              Обзор
            </button>
          </div>

          <button
            type="button"
            onClick={() => setLegendOpen((v) => !v)}
            aria-expanded={legendOpen}
            className={`atlas-glass h-8 rounded-full px-3 text-[11.5px] transition ${
              legendOpen
                ? "border-[var(--accent)]/60 text-[var(--accent)]"
                : "text-[var(--muted)] hover:text-[var(--ink)]"
            }`}
          >
            Легенда
          </button>

          {hoveredNodeObj && !inspectorOpen && (
            <div className="atlas-glass hidden max-w-[320px] items-center gap-2 rounded-full px-3 py-1.5 text-[11.5px] md:flex">
              <span className="truncate text-[var(--ink)]">
                {hoveredNodeObj.title}
              </span>
              {hoveredNodeObj.score != null && (
                <span className="shrink-0 text-[var(--accent)]">
                  {hoveredNodeObj.score}/10
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Right Inspector Panel (Desktop) / Bottom Sheet (Mobile) */}
      {inspectorOpen && (
        <aside
          aria-label="Инспектор атласа"
          className="atlas-inspector fixed inset-x-2 bottom-[calc(var(--app-tabbar-h)+0.5rem)] z-30 flex max-h-[56dvh] flex-col overflow-hidden rounded-2xl md:inset-x-auto md:bottom-4 md:right-4 md:top-[4.4rem] md:max-h-none md:w-[356px]"
        >
          {/* Mobile drag handle indicator */}
          <div className="mx-auto mt-2 h-1 w-9 shrink-0 rounded-full bg-white/20 md:hidden" />

          {/* STATE A: NODE INSPECTOR (Material or Client) */}
          {selectedNode ? (
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              <div className="flex items-center justify-between gap-2 border-b border-white/[0.08] px-4 py-3">
                <div className="flex min-w-0 items-center gap-2">
                  {selectedNode.type === "client" ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--accent)]/40 bg-[var(--accent)]/15 px-2.5 py-0.5 text-[11px] font-medium text-[var(--accent)]">
                      <span className="h-1.5 w-1.5 rotate-45 bg-[var(--accent)]" />
                      Клиент
                    </span>
                  ) : selectedNode.cluster_id &&
                    clusterById.get(selectedNode.cluster_id) ? (
                    <button
                      type="button"
                      onClick={() =>
                        handleSelectCluster(selectedNode.cluster_id!)
                      }
                      className="inline-flex max-w-[230px] items-center gap-1.5 rounded-full border border-white/12 bg-white/[0.04] px-2.5 py-0.5 text-[11px] text-[var(--ink)] transition hover:border-[var(--accent)]/50"
                      title="Перейти к кластеру"
                    >
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{
                          backgroundColor:
                            clusterById.get(selectedNode.cluster_id!)?.color ??
                            "#6c7570",
                        }}
                      />
                      <span className="truncate">
                        {clusterById.get(selectedNode.cluster_id!)?.label}
                      </span>
                    </button>
                  ) : (
                    <span className="text-[11px] text-[var(--muted)]">
                      {nodeTypeLabel(selectedNode.type)}
                    </span>
                  )}
                </div>

                <button
                  type="button"
                  onClick={() => {
                    setSelectedNodeId(null);
                    setIsolateEgo(false);
                  }}
                  aria-label="Закрыть инспектор"
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/[0.06] text-sm text-[var(--muted)] hover:bg-white/[0.12] hover:text-[var(--ink)]"
                >
                  ×
                </button>
              </div>

              <div className="min-h-0 min-w-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-4 py-3.5">
                <div className="min-w-0">
                  <h2 className="break-words [overflow-wrap:anywhere] text-[15.5px] font-semibold leading-snug text-[var(--ink)]">
                    {selectedNode.title}
                  </h2>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-[var(--muted)]">
                    {selectedNode.score != null && (
                      <span className="font-medium text-[var(--accent)]">
                        Оценка {selectedNode.score}/10
                      </span>
                    )}
                    {selectedNode.source_type && (
                      <span>· {sourceLabel(selectedNode.source_type)}</span>
                    )}
                    {formatDate(selectedNode.created_at) && (
                      <span>· {formatDate(selectedNode.created_at)}</span>
                    )}
                  </div>
                </div>

                {selectedNode.summary && (
                  <p className="break-words [overflow-wrap:anywhere] text-[12.5px] leading-relaxed text-[var(--ink)]/82">
                    {selectedNode.summary}
                  </p>
                )}

                {/* Tools & Tags chips */}
                {((selectedNode.tools && selectedNode.tools.length > 0) ||
                  (selectedNode.tags && selectedNode.tags.length > 0)) && (
                  <div className="flex min-w-0 flex-wrap gap-1.5">
                    {(selectedNode.tools || []).slice(0, 8).map((tool) => {
                      const active =
                        toolFilter.toLowerCase() === tool.toLowerCase();
                      return (
                        <button
                          key={`tool-${tool}`}
                          type="button"
                          onClick={() =>
                            setToolFilter(active ? "" : tool.toLowerCase())
                          }
                          className={`max-w-full break-words [overflow-wrap:anywhere] rounded-full border px-2.5 py-0.5 text-left text-[11px] transition ${
                            active
                              ? "border-[var(--accent)] bg-[var(--accent)]/20 text-[var(--accent)]"
                              : "border-[var(--accent)]/25 bg-[var(--accent)]/[0.08] text-[var(--accent)]/90 hover:border-[var(--accent)]/60"
                          }`}
                        >
                          {tool}
                        </button>
                      );
                    })}
                    {(selectedNode.tags || []).slice(0, 8).map((tg) => {
                      const active =
                        tagFilter.toLowerCase() === tg.toLowerCase();
                      return (
                        <button
                          key={`tag-${tg}`}
                          type="button"
                          onClick={() =>
                            setTagFilter(active ? "" : tg.toLowerCase())
                          }
                          className={`max-w-full break-words [overflow-wrap:anywhere] rounded-full border px-2.5 py-0.5 text-left text-[11px] transition ${
                            active
                              ? "border-white/50 bg-white/15 text-[var(--ink)]"
                              : "border-white/12 bg-white/[0.04] text-[var(--muted)] hover:border-white/30 hover:text-[var(--ink)]"
                          }`}
                        >
                          #{tg}
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Primary CTA + Isolate 1-hop toggle */}
                <div className="flex items-center gap-2">
                  <Link
                    href={
                      selectedNode.type === "client"
                        ? `/clients/${selectedNode.id}`
                        : `/m/${selectedNode.id}`
                    }
                    className="flex-1 rounded-full bg-[var(--accent)] px-4 py-2 text-center text-[12.5px] font-semibold text-[var(--btn-fg)] transition hover:brightness-105"
                  >
                    {selectedNode.type === "client"
                      ? "Открыть клиента"
                      : "Открыть материал"}
                  </Link>
                  <button
                    type="button"
                    onClick={toggleIsolateEgo}
                    className={`rounded-full border px-3 py-2 text-[12px] font-medium transition ${
                      isolateEgo
                        ? "border-[var(--accent)] bg-[var(--accent)]/20 text-[var(--accent)]"
                        : "border-white/14 bg-white/[0.04] text-[var(--ink)] hover:border-white/30"
                    }`}
                  >
                    {isolateEgo ? "Показать все" : "1-hop фокус"}
                  </button>
                </div>

                {/* Grouped 1-hop Neighbors */}
                {groupedNeighbors && (
                  <div className="space-y-3 pt-1">
                    {(
                      [
                        {
                          key: "shared_tool" as const,
                          title: "По инструментам",
                          color: EDGE_COLORS.shared_tool,
                        },
                        {
                          key: "semantic" as const,
                          title: "По смыслу",
                          color: EDGE_COLORS.semantic,
                        },
                        {
                          key: "client" as const,
                          title: "Связанные клиенты",
                          color: EDGE_COLORS.client,
                        },
                        {
                          key: "link" as const,
                          title: "Прямые ссылки",
                          color: EDGE_COLORS.link,
                        },
                      ] as const
                    ).map((sec) => {
                      const items = groupedNeighbors[sec.key];
                      if (items.length === 0) return null;
                      return (
                        <div key={sec.key}>
                          <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] font-medium uppercase tracking-[0.13em] text-[var(--muted)]">
                            <span
                              className="h-1.5 w-3 rounded-full"
                              style={{ backgroundColor: sec.color }}
                            />
                            <span>
                              {sec.title} ({items.length})
                            </span>
                          </div>
                          <div className="space-y-1">
                            {items.map((rel) => (
                              <button
                                key={`${sec.key}-${rel.node.id}`}
                                type="button"
                                onClick={() => handleSelectNode(rel.node.id)}
                                className="flex w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-white/[0.06] bg-white/[0.025] px-2.5 py-2 text-left transition hover:border-white/20 hover:bg-white/[0.06]"
                              >
                                <span className="min-w-0 flex-1 truncate text-[12.5px] text-[var(--ink)]">
                                  {rel.node.title}
                                </span>
                                {rel.badge && (
                                  <span
                                    className="max-w-[45%] shrink-0 truncate rounded-full px-2 py-0.5 text-[10.5px]"
                                    style={{
                                      color: sec.color,
                                      backgroundColor: "rgba(255,255,255,0.05)",
                                    }}
                                  >
                                    {rel.badge}
                                  </span>
                                )}
                              </button>
                            ))}
                          </div>
                        </div>
                      );
                    })}

                    {groupedNeighbors.shared_tool.length === 0 &&
                      groupedNeighbors.semantic.length === 0 &&
                      groupedNeighbors.client.length === 0 &&
                      groupedNeighbors.link.length === 0 && (
                        <p className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 text-[12px] text-[var(--muted)]">
                          Пока нет прямых связей с другими материалами.
                        </p>
                      )}
                  </div>
                )}
              </div>
            </div>
          ) : selectedCluster ? (
            /* STATE B: CLUSTER INSPECTOR */
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              <div className="flex items-start justify-between gap-2 border-b border-white/[0.08] px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-3 w-3 shrink-0 rounded-full"
                      style={{ backgroundColor: selectedCluster.color }}
                    />
                    <h2 className="truncate text-[15px] font-semibold text-[var(--ink)]">
                      {selectedCluster.label}
                    </h2>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[11.5px] text-[var(--muted)]">
                    <span>
                      {ruCount(
                        clusterMaterials.length,
                        "материал",
                        "материала",
                        "материалов"
                      )}
                    </span>
                    {selectedCluster.avg_score != null && (
                      <span>
                        · ср. оценка{" "}
                        <strong className="font-medium text-[var(--accent)]">
                          {selectedCluster.avg_score.toFixed(1)}
                        </strong>
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={handleOverview}
                    className="rounded-full bg-white/[0.06] px-2.5 py-1 text-[11px] text-[var(--ink)] hover:bg-white/[0.12]"
                  >
                    Обзор
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedClusterId(null)}
                    aria-label="Закрыть инспектор кластера"
                    className="flex h-7 w-7 items-center justify-center rounded-full bg-white/[0.06] text-sm text-[var(--muted)] hover:bg-white/[0.12] hover:text-[var(--ink)]"
                  >
                    ×
                  </button>
                </div>
              </div>

              <div className="min-h-0 min-w-0 flex-1 space-y-3.5 overflow-y-auto overflow-x-hidden px-4 py-3.5">
                {/* Sibling subclusters switcher for big categories like AI */}
                {siblingSubclusters.length > 1 && (
                  <div>
                    <div className="mb-1.5 text-[10.5px] font-medium uppercase tracking-[0.13em] text-[var(--muted)]">
                      Подкластеры {selectedCluster.category} (
                      {siblingSubclusters.length})
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {siblingSubclusters.map((sub) => {
                        const active = sub.id === selectedCluster.id;
                        return (
                          <button
                            key={sub.id}
                            type="button"
                            onClick={() => handleSelectCluster(sub.id)}
                            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] transition ${
                              active
                                ? "border-[var(--accent)] bg-[var(--accent)]/18 font-medium text-[var(--ink)]"
                                : "border-white/10 bg-white/[0.03] text-[var(--muted)] hover:border-white/25 hover:text-[var(--ink)]"
                            }`}
                          >
                            <span
                              className="h-2 w-2 rounded-full"
                              style={{ backgroundColor: sub.color }}
                            />
                            <span>
                              {shortSubclusterName(sub.label, sub.category)}
                            </span>
                            <span className="opacity-70">{sub.count}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Top tools & tags */}
                {(selectedCluster.top_tools.length > 0 ||
                  selectedCluster.top_tags.length > 0) && (
                  <div className="flex flex-wrap gap-1.5">
                    {selectedCluster.top_tools.slice(0, 6).map((t) => {
                      const active = toolFilter.toLowerCase() === t.toLowerCase();
                      return (
                        <button
                          key={`ctool-${t}`}
                          type="button"
                          onClick={() =>
                            setToolFilter(active ? "" : t.toLowerCase())
                          }
                          className={`max-w-full break-words [overflow-wrap:anywhere] rounded-full border px-2.5 py-0.5 text-left text-[11px] transition ${
                            active
                              ? "border-[var(--accent)] bg-[var(--accent)]/20 text-[var(--accent)]"
                              : "border-[var(--accent)]/25 bg-[var(--accent)]/[0.08] text-[var(--accent)]/90 hover:border-[var(--accent)]/60"
                          }`}
                        >
                          {t}
                        </button>
                      );
                    })}
                    {selectedCluster.top_tags.slice(0, 5).map((tg) => {
                      const active = tagFilter.toLowerCase() === tg.toLowerCase();
                      return (
                        <button
                          key={`ctag-${tg}`}
                          type="button"
                          onClick={() =>
                            setTagFilter(active ? "" : tg.toLowerCase())
                          }
                          className={`max-w-full break-words [overflow-wrap:anywhere] rounded-full border px-2.5 py-0.5 text-left text-[11px] transition ${
                            active
                              ? "border-white/50 bg-white/15 text-[var(--ink)]"
                              : "border-white/12 bg-white/[0.04] text-[var(--muted)] hover:border-white/30 hover:text-[var(--ink)]"
                          }`}
                        >
                          #{tg}
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Sort bar + Full Material List */}
                <div>
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-[10.5px] font-medium uppercase tracking-[0.13em] text-[var(--muted)]">
                      Все материалы ({clusterMaterials.length})
                    </span>
                    <div className="inline-flex rounded-full bg-white/[0.05] p-0.5 text-[11px]">
                      <button
                        type="button"
                        onClick={() => setClusterSort("score")}
                        className={`rounded-full px-2 py-0.5 transition ${
                          clusterSort === "score"
                            ? "bg-[var(--accent)]/20 text-[var(--accent)]"
                            : "text-[var(--muted)]"
                        }`}
                      >
                        По оценке
                      </button>
                      <button
                        type="button"
                        onClick={() => setClusterSort("recent")}
                        className={`rounded-full px-2 py-0.5 transition ${
                          clusterSort === "recent"
                            ? "bg-[var(--accent)]/20 text-[var(--accent)]"
                            : "text-[var(--muted)]"
                        }`}
                      >
                        Новые
                      </button>
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    {clusterMaterials.map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        onClick={() => handleSelectNode(m.id)}
                        className="group flex w-full min-w-0 flex-col gap-1 rounded-xl border border-white/[0.06] bg-white/[0.025] px-3 py-2 text-left transition hover:border-[var(--accent)]/40 hover:bg-white/[0.06]"
                      >
                        <div className="flex w-full min-w-0 items-start justify-between gap-2">
                          <span className="min-w-0 flex-1 line-clamp-2 break-words [overflow-wrap:anywhere] text-[12.5px] leading-snug text-[var(--ink)] group-hover:text-white">
                            {m.title}
                          </span>
                          {m.score != null && (
                            <span
                              className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10.5px] font-medium ${
                                m.score >= 8
                                  ? "bg-[var(--accent)]/20 text-[var(--accent)]"
                                  : "bg-white/[0.06] text-[var(--muted)]"
                              }`}
                            >
                              {m.score}
                            </span>
                          )}
                        </div>
                        <div className="flex w-full min-w-0 items-center gap-1.5 text-[10.5px] text-[var(--muted)]">
                          <span className="shrink-0">
                            {sourceLabel(m.source_type)}
                          </span>
                          {m.tools && m.tools.length > 0 && (
                            <span className="min-w-0 truncate">
                              · {m.tools.slice(0, 2).join(", ")}
                            </span>
                          )}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </aside>
      )}
    </div>
  );
}
