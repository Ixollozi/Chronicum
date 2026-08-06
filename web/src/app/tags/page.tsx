"use client";

import { useCallback, useEffect, useState } from "react";
import { client } from "@/lib/api";

type TagRow = { id: number; name: string; status: string };

export default function TagsPage() {
  const [pending, setPending] = useState<TagRow[]>([]);
  const [approved, setApproved] = useState<TagRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, a] = await Promise.all([client.tags("pending"), client.tags("approved")]);
      setPending(p);
      setApproved(a);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function act(id: number, kind: "approve" | "reject") {
    setBusy(id);
    try {
      if (kind === "approve") await client.approveTag(id);
      else await client.rejectTag(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="grid gap-10 lg:grid-cols-[1.1fr_0.9fr]">
      <div className="animate-rise">
        <h1
          className="text-[var(--text-h1)] tracking-[-0.03em]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          Теги
        </h1>
        <p className="mt-3 max-w-xl text-[var(--muted)]">
          AI предлагает новые теги в статусе pending. Подтверди или отклони — словарь не разъедется.
        </p>
        {error && <p className="mt-4 text-sm text-[var(--warn)]">{error}</p>}
      </div>

      <div className="space-y-8 animate-fade">
        <div className="border-t border-[var(--line)] pt-6">
          <h2 className="mb-4 text-sm uppercase tracking-[0.16em] text-[var(--muted)]">
            Pending ({pending.length})
          </h2>
          {pending.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">Нет тегов на проверку.</p>
          ) : (
            <ul className="space-y-3">
              {pending.map((t) => (
                <li
                  key={t.id}
                  className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] pb-3"
                >
                  <span>{t.name}</span>
                  <span className="flex gap-2">
                    <button
                      disabled={busy === t.id}
                      onClick={() => act(t.id, "approve")}
                      className="border border-[var(--accent)] px-3 py-1 text-sm text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--btn-fg)] disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      disabled={busy === t.id}
                      onClick={() => act(t.id, "reject")}
                      className="border border-[var(--line)] px-3 py-1 text-sm text-[var(--muted)] hover:border-[var(--warn)] hover:text-[var(--warn)] disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="border-t border-[var(--line)] pt-6">
          <h2 className="mb-4 text-sm uppercase tracking-[0.16em] text-[var(--muted)]">
            Approved ({approved.length})
          </h2>
          {approved.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">Словарь пуст.</p>
          ) : (
            <p className="text-sm leading-relaxed text-[var(--muted)]">
              {approved.map((t) => t.name).join(" · ")}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
