"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { client, type ComposerResult, type GraphPayload, type MaterialListItem } from "@/lib/api";
import { nodeTypeLabel, searchGraphNodes } from "@/lib/graphSearch";

function autosizeTextarea(el: HTMLTextAreaElement) {
  el.style.height = "0px";
  const next = Math.min(el.scrollHeight, 160);
  el.style.height = `${Math.max(next, 44)}px`;
  el.style.overflowY = el.scrollHeight > 160 ? "auto" : "hidden";
}

function looksLikeUrlOrNote(text: string): boolean {
  const t = text.trim();
  if (/^https?:\/\//i.test(t)) return true;
  if (/\n/.test(t) && t.length > 120) return true;
  return false;
}

export default function HomePage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLFormElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const suggestRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ComposerResult | null>(null);
  const [graphNodes, setGraphNodes] = useState<GraphPayload["nodes"]>([]);
  const [showSuggest, setShowSuggest] = useState(false);

  const hasSearchAnswer = result?.intent === "search";
  const refuseMessage = result?.intent === "refuse" ? result.answer : null;
  const hasAnswer = hasSearchAnswer;

  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const sync = () => {
      const kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      document.documentElement.style.setProperty("--kb-inset", `${kb}px`);
    };
    vv.addEventListener("resize", sync);
    vv.addEventListener("scroll", sync);
    sync();
    return () => {
      vv.removeEventListener("resize", sync);
      vv.removeEventListener("scroll", sync);
      document.documentElement.style.removeProperty("--kb-inset");
    };
  }, []);

  useEffect(() => {
    if (textRef.current) autosizeTextarea(textRef.current);
  }, [text]);

  useEffect(() => {
    let alive = true;
    client
      .graph()
      .then((g) => {
        if (alive) setGraphNodes(g.nodes);
      })
      .catch(() => {
        /* suggestions are optional */
      });
    return () => {
      alive = false;
    };
  }, []);

  const networkHits = useMemo(() => {
    if (hasAnswer || looksLikeUrlOrNote(text) || text.trim().length < 2) return [];
    return searchGraphNodes(graphNodes, text, 10);
  }, [graphNodes, text, hasAnswer]);

  const suggestOpen = showSuggest && !hasAnswer && networkHits.length > 0;

  useEffect(() => {
    if (!suggestOpen) return;
    function onPointer(e: MouseEvent) {
      const t = e.target as Node;
      if (composerRef.current?.contains(t)) return;
      setShowSuggest(false);
    }
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [suggestOpen]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setShowSuggest(false);
    try {
      const res = await client.composer(value);
      setResult(res);
      if (res.intent === "search" || res.intent === "refuse") {
        return;
      }
      if (res.material_id) {
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
    setShowSuggest(false);
    try {
      const kind = file.type.startsWith("audio/")
        ? "voice"
        : file.type.startsWith("video/")
          ? "video"
          : undefined;
      const res = await client.upload(file, kind);
      router.push(`/m/${res.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки");
    } finally {
      setBusy(false);
    }
  }

  function openInNetwork(id: string) {
    setShowSuggest(false);
    router.push(`/graph?focus=${encodeURIComponent(id)}`);
  }

  return (
    <section
      className={`home-page flex flex-col pb-4 md:min-h-[70dvh] md:pb-6 ${
        hasAnswer
          ? "home-page--docked min-h-[calc(100dvh-var(--app-tabbar-h)-1rem)] justify-start pt-2"
          : "min-h-[calc(100dvh-var(--app-tabbar-h)-2rem)] justify-center"
      }`}
    >
      {!hasAnswer && (
        <div className="home-hero mx-auto w-full max-w-2xl text-center animate-rise">
          <h1
            className="home-hero-title text-[clamp(2.8rem,10vw,4.75rem)] leading-[0.92] tracking-[-0.04em]"
            style={{ fontFamily: "var(--font-display)" }}
          >
            Chronicum
          </h1>
          <p className="home-hero-sub mx-auto mt-4 max-w-md text-[var(--muted)]">
            Что сегодня в повестке? Спроси базу, кинь ссылку или сохрани мысль — разберу сам.
          </p>
        </div>
      )}

      {hasSearchAnswer && (
        <div className="home-answer mx-auto mb-4 w-full max-w-2xl flex-1 animate-fade overflow-y-auto">
          <div className="mb-4 flex items-center justify-between gap-3">
            <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Ответ</p>
            <button
              type="button"
              className="rounded-full px-3 py-1.5 text-xs text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--ink)]"
              onClick={() => {
                setResult(null);
                setText("");
              }}
            >
              Новый вопрос
            </button>
          </div>
          <div className="whitespace-pre-wrap leading-relaxed text-[var(--ink)]">{result.answer}</div>
          {result.sources?.length > 0 && (
            <ul className="mt-8 space-y-1 border-t border-[var(--line)] pt-5">
              <li className="mb-2 text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Источники</li>
              {result.sources.map((s: MaterialListItem) => (
                <li key={s.id}>
                  <Link
                    href={`/m/${s.id}`}
                    className="group flex min-h-12 items-center justify-between gap-3 border-b border-[var(--line)] py-3"
                  >
                    <span className="min-w-0">
                      <span className="block text-[var(--accent)] group-hover:underline">
                        {s.title || "Без названия"}
                      </span>
                      <span className="mt-0.5 block text-sm text-[var(--muted)] line-clamp-2">{s.summary}</span>
                    </span>
                    <span className="shrink-0 text-[var(--muted)] opacity-50" aria-hidden>
                      ›
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <form
        ref={composerRef}
        onSubmit={onSubmit}
        className={`home-composer relative z-20 mx-auto w-full max-w-2xl animate-rise ${
          hasSearchAnswer ? "home-composer--dock mt-auto" : "mt-10"
        }`}
        style={{ animationDelay: "90ms" }}
      >
        {refuseMessage && (
          <div className="home-refuse-banner" role="status">
            <div className="home-refuse-banner-head">
              <span>Не сохранено</span>
              <button
                type="button"
                className="home-refuse-banner-close"
                aria-label="Закрыть"
                onClick={() => setResult(null)}
              >
                ✕
              </button>
            </div>
            <p className="home-refuse-banner-text">{refuseMessage}</p>
          </div>
        )}

        <div className="composer-shell flex items-end gap-2 rounded-[1.35rem] border border-[var(--line)] bg-[var(--surface)] px-3 py-2 shadow-[0_20px_60px_rgba(0,0,0,0.28)] transition focus-within:border-[var(--accent)] focus-within:bg-[var(--surface-hover)]">
          <button
            type="button"
            aria-label="Прикрепить файл или аудио"
            title="Файл, аудио или видео"
            onClick={() => fileRef.current?.click()}
            className="mb-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-[var(--line)] text-lg text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
          >
            +
          </button>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            accept="audio/*,video/*,image/*,.pdf,.mp3,.wav,.m4a,.ogg,.oga,.opus,.flac,.aac,.wma,.aiff,.aif,.caf,.amr,.webm,.mp4,.mov,.mkv,.png,.jpg,.jpeg,.webp,.gif"
            onChange={(e) => {
              const f = e.target.files?.[0] || null;
              e.target.value = "";
              void onFile(f);
            }}
          />
          <textarea
            ref={textRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setShowSuggest(true);
              if (result?.intent === "refuse") setResult(null);
            }}
            rows={1}
            enterKeyHint="send"
            placeholder={hasSearchAnswer ? "Уточните или задайте новый вопрос…" : "Спросите Chronicum или вставьте ссылку…"}
            className="composer-input max-h-40 min-h-[44px] w-full resize-none overflow-hidden bg-transparent py-2.5 outline-none placeholder:text-[var(--muted)]"
            onFocus={() => setShowSuggest(true)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setShowSuggest(false);
                if (result?.intent === "refuse") setResult(null);
                return;
              }
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void onSubmit(e as unknown as FormEvent);
              }
            }}
          />
          <button
            type="submit"
            disabled={busy || !text.trim()}
            aria-label={busy ? "Отправка" : "Отправить"}
            className="mb-1 flex h-11 min-w-11 shrink-0 items-center justify-center rounded-full border border-[var(--accent)] bg-[var(--accent)] px-3.5 text-sm text-[var(--btn-fg)] disabled:opacity-40"
          >
            {busy ? "…" : "↑"}
          </button>
        </div>

        {suggestOpen && (
          <div ref={suggestRef} className="home-net-suggest" role="listbox" aria-label="Точки сети">
            <p className="home-net-suggest-kicker">В сети</p>
            <ul className="home-net-suggest-list">
              {networkHits.map((hit) => (
                <li key={hit.id}>
                  <button
                    type="button"
                    role="option"
                    className="home-net-suggest-item"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => openInNetwork(hit.id)}
                  >
                    <span className="home-net-suggest-title">{hit.title}</span>
                    <span className="home-net-suggest-meta">{nodeTypeLabel(hit.type)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {!hasSearchAnswer && !suggestOpen && !refuseMessage && (
          <p className="composer-kbd-hint composer-kbd-desktop mt-3 text-center text-xs text-[var(--muted)]">
            Enter — отправить · Shift+Enter — новая строка
          </p>
        )}
      </form>

      {error && (
        <p className="mx-auto mt-4 max-w-2xl border-l-2 border-[var(--warn)] pl-3 text-sm text-[var(--warn)]">
          {error}
        </p>
      )}
    </section>
  );
}
