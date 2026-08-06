"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { MaterialRow } from "@/components/MaterialRow";
import { client, type MaterialListItem } from "@/lib/api";

export default function ArchivePage() {
  const [items, setItems] = useState<MaterialListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<{ id: number; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

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
    client.categories().then(setCategories).catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const busyItems = items.some((i) => ["queued", "processing"].includes(i.processing_status));
    if (!busyItems) return;
    const t = setInterval(() => load({ silent: true }), 4000);
    return () => clearInterval(t);
  }, [items, load]);

  const allSelected = useMemo(
    () => items.length > 0 && items.every((i) => selected.has(i.id)),
    [items, selected]
  );

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    if (allSelected) setSelected(new Set());
    else setSelected(new Set(items.map((i) => i.id)));
  }

  async function deleteSelected() {
    const ids = [...selected];
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
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <p className="text-[var(--muted)]">{total} материалов</p>
        {selected.size > 0 && (
          <button
            type="button"
            disabled={busy}
            onClick={deleteSelected}
            className="border border-[var(--warn)] px-3 py-1.5 text-sm text-[var(--warn)] hover:bg-[var(--warn)] hover:text-[var(--btn-fg)] disabled:opacity-40"
          >
            {busy ? "…" : `Удалить выбранные (${selected.size})`}
          </button>
        )}
      </div>
      <form
        className="mb-8 flex flex-col gap-3 sm:flex-row sm:flex-wrap"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Фильтр…"
          className="min-w-[200px] border-b border-[var(--line)] bg-transparent py-2 outline-none focus:border-[var(--accent)]"
        />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="border-b border-[var(--line)] bg-transparent py-2 outline-none"
        >
          <option value="">Все статусы</option>
          <option value="done">готово</option>
          <option value="queued">очередь</option>
          <option value="processing">обработка</option>
          <option value="awaiting_full">ждёт full</option>
          <option value="failed">ошибка</option>
        </select>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="border-b border-[var(--line)] bg-transparent py-2 outline-none"
        >
          <option value="">Все категории</option>
          {categories.map((c) => (
            <option key={c.id} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
        <button type="submit" className="border border-[var(--line)] px-4 py-2 text-sm hover:border-[var(--accent)]">
          Найти
        </button>
      </form>

      {error && <p className="mb-4 text-sm text-[var(--warn)]">{error}</p>}
      {loading ? (
        <p className="text-[var(--muted)]">Загружаю…</p>
      ) : items.length === 0 ? (
        <p className="text-[var(--muted)]">Пусто. Начни с главной строки.</p>
      ) : (
        <div>
          <label className="mb-2 flex items-center gap-2 text-xs text-[var(--muted)]">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              className="accent-[var(--accent)]"
            />
            Выбрать все на странице
          </label>
          {items.map((item, i) => (
            <MaterialRow
              key={item.id}
              item={item}
              index={i}
              selectable
              selected={selected.has(item.id)}
              onToggle={toggle}
            />
          ))}
        </div>
      )}
    </section>
  );
}
