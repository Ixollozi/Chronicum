"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArchivePreview } from "@/components/ArchivePreview";
import { FancySelect } from "@/components/FancySelect";
import {
  MaterialRow,
  SOURCE_LABEL,
  StatusPill,
  cleanMetaPart,
  formatShortDate,
} from "@/components/MaterialRow";
import { client, type MaterialListItem } from "@/lib/api";

const STATUSES = [
  { value: "", label: "Все" },
  { value: "done", label: "Готово" },
  { value: "queued", label: "Очередь" },
  { value: "processing", label: "В работе" },
  { value: "awaiting_full", label: "Ждёт full" },
  { value: "failed", label: "Ошибка" },
] as const;

function materialsLabel(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} материал`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} материала`;
  return `${n} материалов`;
}

function cleanCategories(list: { id: number; name: string }[]) {
  return list.filter((c) => {
    const n = c.name.trim().toLowerCase();
    return n && n !== "none" && n !== "n/a" && n !== "null";
  });
}

export default function ArchivePage() {
  const [items, setItems] = useState<MaterialListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [draftQ, setDraftQ] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<{ id: number; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectMode, setSelectMode] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  const load = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!opts?.silent) setLoading(true);
      setError(null);
      try {
        const data = await client.listMaterials({
          q: q || undefined,
          status: status || undefined,
          category: category || undefined,
          limit: 50,
        });
        setItems(data.items);
        setTotal(data.total);
        setSelected((prev) => {
          const ids = new Set(data.items.map((i) => i.id));
          return new Set([...prev].filter((id) => ids.has(id)));
        });
        setActiveId((prev) => {
          if (prev && data.items.some((i) => i.id === prev)) return prev;
          return data.items[0]?.id ?? null;
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Ошибка загрузки");
      } finally {
        if (!opts?.silent) setLoading(false);
      }
    },
    [q, status, category]
  );

  useEffect(() => {
    load();
    client
      .categories()
      .then((c) => setCategories(cleanCategories(c)))
      .catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const t = setTimeout(() => setQ(draftQ.trim()), 320);
    return () => clearTimeout(t);
  }, [draftQ]);

  useEffect(() => {
    const busyItems = items.some((i) => ["queued", "processing"].includes(i.processing_status));
    if (!busyItems) return;
    const t = setInterval(() => load({ silent: true }), 4000);
    return () => clearInterval(t);
  }, [items, load]);

  const activeItem = useMemo(
    () => items.find((i) => i.id === activeId) ?? null,
    [items, activeId]
  );

  const allSelected = useMemo(
    () => items.length > 0 && items.every((i) => selected.has(i.id)),
    [items, selected]
  );

  const processingCount = useMemo(
    () => items.filter((i) => ["queued", "processing"].includes(i.processing_status)).length,
    [items]
  );

  const filtersActive = Boolean(q || status || category);
  const showChecks = selectMode || selected.size > 0;

  function toggle(id: string) {
    setSelectMode(true);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function exitSelectMode() {
    setSelectMode(false);
    setSelected(new Set());
  }

  function pickItem(id: string) {
    setActiveId(id);
    if (showChecks) return;
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) {
      setSheetOpen(true);
    }
  }

  function closeSheet() {
    setSheetOpen(false);
  }

  function toggleAll() {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(items.map((i) => i.id)));
  }

  function clearFilters() {
    setDraftQ("");
    setQ("");
    setStatus("");
    setCategory("");
  }

  const itemsRef = useRef(items);
  const activeRef = useRef(activeId);
  itemsRef.current = items;
  activeRef.current = activeId;

  const moveActive = useCallback((delta: number) => {
    const list = itemsRef.current;
    if (!list.length) return;
    const idx = list.findIndex((i) => i.id === activeRef.current);
    const base = idx < 0 ? 0 : idx;
    const next = Math.min(list.length - 1, Math.max(0, base + delta));
    setActiveId(list[next].id);
    const el = listRef.current?.querySelector(`[data-id="${list[next].id}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.tagName === "SELECT" ||
          t.isContentEditable)
      ) {
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        moveActive(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        moveActive(-1);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moveActive]);

  async function deleteIds(ids: string[]) {
    if (!ids.length) return;
    const ok = window.confirm(
      ids.length === 1
        ? "Удалить материал безвозвратно?"
        : `Удалить ${ids.length} материалов безвозвратно?`
    );
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await client.deleteMaterials(ids);
      setSelected(new Set());
      setSelectMode(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="animate-fade">
      {/* Browser chrome */}
      <div className="lib-chrome mb-4">
        <div className="mb-2.5 px-1 md:mb-3 md:px-0">
          <p className="lib-chrome-eyebrow mb-1 text-[10px] uppercase tracking-[0.28em] text-[var(--muted)]">
            Chronicum
          </p>
          <h1
            className="text-[clamp(1.7rem,3vw,2.15rem)] leading-none tracking-[-0.03em]"
            style={{ fontFamily: "var(--font-display)" }}
          >
            Архив
          </h1>
        </div>

        <div className="flex flex-col gap-2.5 md:gap-3">
          <div className="flex items-center gap-2">
            <form
              className="relative min-w-0 flex-1"
              onSubmit={(e) => {
                e.preventDefault();
                setQ(draftQ.trim());
              }}
            >
              <input
                value={draftQ}
                onChange={(e) => setDraftQ(e.target.value)}
                placeholder="Найти…"
                className="archive-search pr-16 md:pr-24"
                aria-label="Поиск в архиве"
              />
              {draftQ && (
                <button
                  type="button"
                  onClick={() => {
                    setDraftQ("");
                    setQ("");
                  }}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-full px-2 py-1.5 text-xs text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--ink)] md:right-3"
                >
                  ✕
                </button>
              )}
            </form>
            <Link href="/" className="archive-cta shrink-0" aria-label="Добавить материал">
              <span className="md:hidden text-lg leading-none" aria-hidden>
                +
              </span>
              <span className="hidden md:inline">+ Добавить</span>
            </Link>
          </div>

          <div className="archive-filters">
            <div className="archive-pill-scroller">
              {STATUSES.map((s) => (
                <button
                  key={s.value || "all"}
                  type="button"
                  className="archive-pill shrink-0"
                  data-active={status === s.value}
                  onClick={() => setStatus(s.value)}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <div className="archive-filters-row">
              <FancySelect
                aria-label="Категория"
                variant="archive"
                value={category}
                onChange={setCategory}
                options={[
                  { value: "", label: "Все категории" },
                  ...categories.map((c) => ({ value: c.name, label: c.name })),
                ]}
              />
              {filtersActive && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="rounded-full px-3 py-1.5 text-xs text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--accent)]"
                >
                  Сбросить
                </button>
              )}
              {processingCount > 0 && (
                <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-[var(--accent-soft)] px-2.5 py-1 text-xs text-[var(--accent)]">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
                  {processingCount} в работе
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {error && (
        <p className="mb-3 rounded-[var(--radius-md)] border border-[color-mix(in_srgb,var(--warn)_40%,transparent)] bg-[color-mix(in_srgb,var(--warn)_10%,transparent)] px-4 py-3 text-sm text-[var(--warn)]">
          {error}
        </p>
      )}

      {/* Split workspace */}
      <div className="lib-shell">
        <div className="lib-list-pane flex min-h-0 flex-col">
          <div className="lib-list-head">
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="lib-select-all"
                onClick={() => {
                  if (showChecks) exitSelectMode();
                  else setSelectMode(true);
                }}
                disabled={!items.length || loading}
                aria-pressed={showChecks}
              >
                <span className="lib-check" data-checked={showChecks || undefined} aria-hidden />
                <span>{showChecks ? "Готово" : "Выбрать"}</span>
              </button>
              {showChecks && (
                <button
                  type="button"
                  className="rounded-full px-2.5 py-1.5 text-xs text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--ink)]"
                  onClick={toggleAll}
                  disabled={!items.length || loading}
                >
                  {allSelected ? "Снять все" : "Все"}
                </button>
              )}
            </div>
            <p className="tabular-nums text-[var(--muted)]">
              {loading ? "…" : (
                <>
                  <span className="text-[var(--ink)]">{materialsLabel(total)}</span>
                  {items.length !== total && (
                    <span className="opacity-70"> · на экране {items.length}</span>
                  )}
                </>
              )}
            </p>
          </div>

          <div
            ref={listRef}
            role="listbox"
            aria-label="Материалы архива"
            className="lib-list-scroll"
          >
            {loading ? (
              <div className="space-y-0 p-2">
                {[0, 1, 2, 3, 4, 5, 6].map((i) => (
                  <div key={i} className="flex gap-3 px-3 py-3">
                    <div className="archive-skeleton h-4 w-6" />
                    <div className="min-w-0 flex-1 space-y-2">
                      <div className="archive-skeleton h-4 w-3/4" />
                      <div className="archive-skeleton h-3 w-full" />
                    </div>
                  </div>
                ))}
              </div>
            ) : items.length === 0 ? (
              <div className="flex h-full min-h-[16rem] flex-col items-center justify-center px-6 py-12 text-center">
                <p
                  className="text-[1.6rem] tracking-[-0.03em]"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  {filtersActive ? "Ничего не нашлось" : "Полка пуста"}
                </p>
                <p className="mt-2 max-w-xs text-sm text-[var(--muted)]">
                  {filtersActive
                    ? "Сбросьте фильтр или измените запрос."
                    : "Добавьте ссылку или заметку с главной."}
                </p>
                <div className="mt-5">
                  {filtersActive ? (
                    <button type="button" onClick={clearFilters} className="card-btn card-btn-accent">
                      Сбросить
                    </button>
                  ) : (
                    <Link href="/" className="archive-cta">
                      На главную
                    </Link>
                  )}
                </div>
              </div>
            ) : (
              items.map((item, i) => (
                <div key={item.id} data-id={item.id}>
                  <MaterialRow
                    item={item}
                    index={i}
                    active={item.id === activeId}
                    selectable={showChecks}
                    selected={selected.has(item.id)}
                    onSelect={pickItem}
                    onToggle={toggle}
                    forceChecks={showChecks}
                  />
                </div>
              ))
            )}
          </div>
        </div>

        <aside className="lib-preview-pane hidden min-h-0 lg:block">
          <ArchivePreview
            item={activeItem}
            busy={busy}
            onDelete={(id) => deleteIds([id])}
          />
        </aside>
      </div>

      <p className="mt-3 hidden text-[11px] text-[var(--muted)] lg:block">
        ↑↓ — листать список · клик — превью · «Открыть карточку» — полный разбор
      </p>

      {selected.size > 0 && (
        <div className="archive-selection mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm">
            Выбрано <span className="text-[var(--accent)]">{selected.size}</span>
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={exitSelectMode} className="card-btn">
              Снять
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => deleteIds([...selected])}
              className="card-btn card-btn-danger"
            >
              {busy ? "Удаляю…" : "Удалить"}
            </button>
          </div>
        </div>
      )}

      {sheetOpen && activeItem && !showChecks && (
        <div className="archive-sheet lg:hidden" role="dialog" aria-modal="true" aria-label="Действия с материалом">
          <button type="button" className="archive-sheet-backdrop" aria-label="Закрыть" onClick={closeSheet} />
          <div className="archive-sheet-panel animate-rise">
            <div className="archive-sheet-handle" aria-hidden />
            <div className="archive-sheet-head">
              <div className="min-w-0">
                <StatusPill status={activeItem.processing_status} />
                <h2
                  className="mt-2 line-clamp-2 text-[1.25rem] leading-snug tracking-[-0.02em]"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  {activeItem.title || "Без названия"}
                </h2>
                <p className="mt-1.5 text-sm text-[var(--muted)]">
                  {[
                    SOURCE_LABEL[activeItem.source_type] || activeItem.source_type,
                    cleanMetaPart(activeItem.category),
                    formatShortDate(activeItem.created_at),
                    activeItem.usefulness_score != null ? `${activeItem.usefulness_score}/10` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              <button
                type="button"
                className="archive-sheet-close"
                aria-label="Закрыть"
                onClick={closeSheet}
              >
                ✕
              </button>
            </div>
            {activeItem.summary && (
              <p className="mt-3 line-clamp-3 text-sm leading-relaxed text-[color-mix(in_srgb,var(--ink)_88%,transparent)]">
                {activeItem.summary}
              </p>
            )}
            <div className="archive-sheet-actions">
              <Link href={`/m/${activeItem.id}`} className="archive-sheet-btn primary">
                Открыть карточку
              </Link>
              {activeItem.source_url && (
                <a
                  href={activeItem.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="archive-sheet-btn"
                >
                  Источник ↗
                </a>
              )}
              <button
                type="button"
                className="archive-sheet-btn"
                onClick={() => {
                  setSelectMode(true);
                  toggle(activeItem.id);
                  closeSheet();
                }}
              >
                Выбрать
              </button>
              <button
                type="button"
                className="archive-sheet-btn danger"
                disabled={busy}
                onClick={() => {
                  closeSheet();
                  void deleteIds([activeItem.id]);
                }}
              >
                Удалить
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
