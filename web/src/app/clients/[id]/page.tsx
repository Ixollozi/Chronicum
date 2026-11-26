"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { client, type ClientDetail } from "@/lib/api";

export default function ClientThreadPage() {
  const params = useParams();
  const id = String(params?.id || "");
  const [data, setData] = useState<ClientDetail | null>(null);
  const [body, setBody] = useState("");
  const [paste, setPaste] = useState("");
  const [aliases, setAliases] = useState("");
  const [contextLine, setContextLine] = useState("");
  const [canon, setCanon] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setError(null);
      const d = await client.getClient(id);
      setData(d);
      setAliases((d.aliases || []).join(", "));
      setContextLine(d.context_line || "");
      setCanon(d.canon_facts || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onAppend(e: FormEvent) {
    e.preventDefault();
    if (!body.trim() || !id) return;
    setBusy(true);
    try {
      setData(await client.appendClientMessage(id, body.trim(), false));
      setBody("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onRefresh() {
    if (!id) return;
    setBusy(true);
    try {
      await client.refreshClientSummary(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSaveCard() {
    if (!id) return;
    setBusy(true);
    try {
      await client.updateClient(id, {
        aliases: aliases.split(",").map((s) => s.trim()).filter(Boolean),
        context_line: contextLine,
        canon_facts: canon,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onPaste() {
    if (!id || !paste.trim()) return;
    setBusy(true);
    try {
      const prev = await client.pastePreview(id, paste.trim());
      const roles: Record<string, string> = {};
      for (const s of prev.speakers || []) {
        roles[s] = s === data?.display_name ? "client" : "other";
      }
      const res = await client.pasteCommit(id, paste.trim(), roles);
      setData(res);
      setPaste("");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!data && !error) {
    return <p className="p-6 text-sm text-[var(--muted)]">Загрузка…</p>;
  }

  return (
    <main className="mx-auto max-w-2xl px-4 pb-28 pt-6">
      <Link href="/clients" className="text-sm text-[var(--accent)] hover:underline">
        ← Клиенты
      </Link>
      <h1 className="mt-3 text-3xl tracking-tight" style={{ fontFamily: "var(--font-display)" }}>
        {data?.display_name || "Клиент"}
      </h1>
      {data?.context_line && <p className="mt-1 text-sm text-[var(--muted)]">{data.context_line}</p>}
      {error && <p className="mt-3 text-sm text-[var(--warn)]">{error}</p>}

      <section className="mt-6 rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Сводка</h2>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onRefresh()}
            className="rounded-full border border-[var(--line)] px-3 py-1 text-xs hover:border-[var(--accent)] disabled:opacity-40"
          >
            Обновить сводку
          </button>
        </div>
        <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-[var(--ink)]">
          {data?.summary || "Пока пусто — допишите сообщения и нажмите «Обновить сводку»."}
        </p>
        {data?.summary_version ? (
          <p className="mt-2 text-[11px] text-[var(--muted)]">
            v{data.summary_version}
            {data.summary_updated_at
              ? ` · ${new Date(data.summary_updated_at).toLocaleString()}`
              : ""}
            {data.summary_stale
              ? ` · устарела: +${data.messages_since_summary || 0} новых сообщений`
              : ""}
          </p>
        ) : null}
      </section>

      <section className="mt-6 rounded-2xl border border-[var(--line)] p-4">
        <h2 className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Карточка</h2>
        <label className="mt-3 block text-xs text-[var(--muted)]">Алиасы (через запятую)</label>
        <input
          value={aliases}
          onChange={(e) => setAliases(e.target.value)}
          className="mt-1 w-full rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm outline-none"
        />
        <label className="mt-3 block text-xs text-[var(--muted)]">Контекст одной строкой</label>
        <input
          value={contextLine}
          onChange={(e) => setContextLine(e.target.value)}
          className="mt-1 w-full rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm outline-none"
        />
        <label className="mt-3 block text-xs text-[var(--muted)]">
          Зафиксированные факты (канон — LLM не переписывает)
        </label>
        <textarea
          value={canon}
          onChange={(e) => setCanon(e.target.value)}
          rows={3}
          className="mt-1 w-full rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm outline-none"
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => void onSaveCard()}
          className="mt-3 rounded-full border border-[var(--line)] px-4 py-2 text-sm disabled:opacity-40"
        >
          Сохранить карточку
        </button>
      </section>

      <section className="mt-6 rounded-2xl border border-[var(--line)] p-4">
        <h2 className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">
          Вставить переписку
        </h2>
        <textarea
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
          rows={5}
          placeholder="[12.03.2026, 14:02] Иван: …"
          className="mt-3 w-full rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm outline-none"
        />
        <button
          type="button"
          disabled={busy || !paste.trim()}
          onClick={() => void onPaste()}
          className="mt-2 rounded-full bg-[var(--accent)] px-4 py-2 text-sm font-medium text-[var(--btn-fg)] disabled:opacity-40"
        >
          Разобрать и добавить
        </button>
      </section>

      <section className="mt-8">
        <h2 className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Тред</h2>
        <ul className="mt-3 space-y-3">
          {(data?.messages || []).map((m) => (
            <li key={m.id} className="rounded-xl border border-[var(--line)] px-3 py-2.5">
              <p className="whitespace-pre-wrap text-sm">{m.body}</p>
              <p className="mt-1 text-[11px] text-[var(--muted)]">
                {m.speaker_name || m.speaker || "unknown"} · {m.source}
                {(m.event_at || m.created_at)
                  ? ` · ${new Date(m.event_at || m.created_at || "").toLocaleString()}`
                  : ""}
              </p>
            </li>
          ))}
          {!data?.messages?.length && (
            <li className="text-sm text-[var(--muted)]">Сообщений пока нет</li>
          )}
        </ul>
      </section>

      <form
        onSubmit={onAppend}
        className="fixed bottom-[calc(var(--app-tabbar-h)+0.75rem)] left-0 right-0 z-20 mx-auto flex max-w-2xl gap-2 px-4"
      >
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={2}
          placeholder="Дописать сообщение…"
          className="min-w-0 flex-1 rounded-2xl border border-[var(--line)] bg-[var(--bg)] px-4 py-3 text-sm shadow-lg outline-none focus:border-[var(--accent)]"
        />
        <button
          type="submit"
          disabled={busy || !body.trim()}
          className="rounded-full bg-[var(--accent)] px-4 py-3 text-sm font-medium text-[var(--btn-fg)] disabled:opacity-40"
        >
          →
        </button>
      </form>
    </main>
  );
}
