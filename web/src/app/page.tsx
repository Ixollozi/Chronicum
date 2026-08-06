"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useRef, useState } from "react";
import { client, type ComposerResult, type MaterialListItem } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ComposerResult | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await client.composer(value);
      setResult(res);
      if (res.intent !== "search" && res.material_id) {
        setText("");
        router.push(`/m/${res.material_id}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function onFile(file: File | null) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const res = await client.upload(file);
      router.push(`/m/${res.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex min-h-[70dvh] flex-col justify-center pb-6">
      <div className="mx-auto w-full max-w-2xl text-center animate-rise">
        <h1
          className="text-[clamp(2.8rem,10vw,4.75rem)] leading-[0.92] tracking-[-0.04em]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          Chronicum
        </h1>
        <p className="mx-auto mt-4 max-w-md text-[var(--muted)]">
          Что сегодня в повестке? Спроси базу, кинь ссылку или сохрани мысль — разберу сам.
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="mx-auto mt-10 w-full max-w-2xl animate-rise"
        style={{ animationDelay: "90ms" }}
      >
        <div className="composer-shell flex items-end gap-2 rounded-[1.35rem] border border-[var(--line)] bg-[var(--surface)] px-3 py-2 shadow-[0_20px_60px_rgba(0,0,0,0.28)] transition focus-within:border-[var(--accent)] focus-within:bg-[var(--surface-hover)]">
          <button
            type="button"
            aria-label="Прикрепить файл"
            onClick={() => fileRef.current?.click()}
            className="mb-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-[var(--line)] text-lg text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
          >
            +
          </button>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            onChange={(e) => onFile(e.target.files?.[0] || null)}
          />
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={1}
            placeholder="Спросите Chronicum или вставьте ссылку…"
            className="max-h-40 min-h-[44px] w-full resize-none bg-transparent py-2.5 outline-none placeholder:text-[var(--muted)]"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void onSubmit(e as unknown as FormEvent);
              }
            }}
          />
          <button
            type="submit"
            disabled={busy || !text.trim()}
            className="mb-1 shrink-0 rounded-full border border-[var(--accent)] bg-[var(--accent)] px-4 py-2 text-sm text-[var(--btn-fg)] disabled:opacity-40"
          >
            {busy ? "…" : "→"}
          </button>
        </div>
        <p className="mt-3 text-center text-xs text-[var(--muted)]">
          Enter — отправить · Shift+Enter — новая строка ·{" "}
          <Link href="/graph" className="text-[var(--accent)] hover:underline">
            открыть сеть
          </Link>
        </p>
      </form>

      {error && (
        <p className="mx-auto mt-6 max-w-2xl border-l-2 border-[var(--warn)] pl-3 text-sm text-[var(--warn)]">
          {error}
        </p>
      )}

      {result?.intent === "search" && (
        <div className="mx-auto mt-10 w-full max-w-2xl animate-fade">
          <p className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Ответ</p>
          <div className="whitespace-pre-wrap leading-relaxed text-[var(--ink)]">{result.answer}</div>
          {result.sources?.length > 0 && (
            <ul className="mt-8 space-y-3 border-t border-[var(--line)] pt-6">
              <li className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Источники</li>
              {result.sources.map((s: MaterialListItem) => (
                <li key={s.id}>
                  <Link href={`/m/${s.id}`} className="group block border-b border-[var(--line)] py-3">
                    <span className="text-[var(--accent)] group-hover:underline">{s.title || "Без названия"}</span>
                    <span className="mt-1 block text-sm text-[var(--muted)] line-clamp-2">{s.summary}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
