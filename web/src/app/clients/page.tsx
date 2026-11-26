"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { client, type ClientCard } from "@/lib/api";

export default function ClientsPage() {
  const [items, setItems] = useState<ClientCard[]>([]);
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      const rows = await client.listClients(q.trim() || undefined);
      setItems(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [q]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const c = await client.createClient({ display_name: name.trim() });
      setName("");
      window.location.href = `/clients/${c.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-4 pb-24 pt-6">
      <h1 className="text-3xl tracking-tight" style={{ fontFamily: "var(--font-display)" }}>
        Клиенты
      </h1>
      <p className="mt-2 text-sm text-[var(--muted)]">
        Живая переписка: карточка человека, тред и сводка для агента — не CRM.
      </p>

      <form onSubmit={onCreate} className="mt-6 flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Новый клиент — имя"
          className="min-w-0 flex-1 rounded-full border border-[var(--line)] bg-[var(--surface)] px-4 py-2.5 text-sm outline-none focus:border-[var(--accent)]"
        />
        <button
          type="submit"
          disabled={busy || !name.trim()}
          className="rounded-full bg-[var(--accent)] px-4 py-2.5 text-sm font-medium text-[var(--btn-fg)] disabled:opacity-40"
        >
          Создать
        </button>
      </form>

      <div className="mt-4">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Фильтр по имени / алиасу"
          className="w-full rounded-full border border-[var(--line)] bg-[var(--surface)] px-4 py-2.5 text-sm outline-none"
        />
      </div>

      {error && <p className="mt-4 text-sm text-[var(--warn)]">{error}</p>}

      <ul className="mt-6 space-y-1">
        {items.map((c) => (
          <li key={c.id}>
            <Link
              href={`/clients/${c.id}`}
              className="flex items-center justify-between gap-3 border-b border-[var(--line)] py-3"
            >
              <span className="min-w-0">
                <span className="block font-medium text-[var(--ink)]">{c.display_name}</span>
                <span className="mt-0.5 block text-sm text-[var(--muted)] line-clamp-2">
                  {c.context_line || c.summary || "Без сводки"}
                </span>
              </span>
              <span className="shrink-0 text-xs text-[var(--muted)]">{c.message_count} сообщ.</span>
            </Link>
          </li>
        ))}
        {!items.length && !error && (
          <li className="py-8 text-center text-sm text-[var(--muted)]">Пока нет клиентов</li>
        )}
      </ul>
    </main>
  );
}
