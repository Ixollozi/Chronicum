"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { BrandMark } from "@/components/BrandMark";
import { client, type ComposerMode, type ComposerResult, type GraphPayload, type MaterialListItem } from "@/lib/api";
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

const MODES: { id: ComposerMode; label: string; hint: string; placeholder: string }[] = [
  {
    id: "auto",
    label: "Авто",
    hint: "Ссылка / вопрос / заметка",
    placeholder: "Спросите Chronicum или вставьте ссылку…",
  },
  {
    id: "ask",
    label: "Спросить",
    hint: "Ответ по базе",
    placeholder: "Что сохранить из этого? Как я решаю…",
  },
  {
    id: "knowledge",
    label: "Знание",
    hint: "В архив как материал",
    placeholder: "Мысль, конспект или ссылка на материал…",
  },
  {
    id: "task",
    label: "Задача",
    hint: "Сделать → критерий готово",
    placeholder: "Что сделать? Например: оформить лендинг до пятницы…",
  },
  {
    id: "rule",
    label: "Правило",
    hint: "Как работать дальше",
    placeholder: "Правило: всегда… / никогда не…",
  },
];

export default function HomePage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLFormElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const suggestRef = useRef<HTMLDivElement>(null);
  const modeMenuRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<ComposerMode>("auto");
  const [modeOpen, setModeOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ComposerResult | null>(null);
  const [lastQuery, setLastQuery] = useState<string>("");
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [graphNodes, setGraphNodes] = useState<GraphPayload["nodes"]>([]);
  const [showSuggest, setShowSuggest] = useState(false);

  const hasSearchAnswer = result?.intent === "search";
  const refuseMessage = result?.intent === "refuse" ? result.answer : null;
  const hasAnswer = hasSearchAnswer;
  const activeMode = MODES.find((m) => m.id === mode) || MODES[0]!;
  const composerPlaceholder = hasSearchAnswer
    ? "Уточните или задайте новый вопрос…"
    : activeMode.placeholder;

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
      .graph({ light: true })
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
    if (mode !== "auto" && mode !== "ask") return [];
    if (hasAnswer || looksLikeUrlOrNote(text) || text.trim().length < 2) return [];
    return searchGraphNodes(graphNodes, text, 10);
  }, [graphNodes, text, hasAnswer, mode]);

  const suggestOpen = showSuggest && !hasAnswer && networkHits.length > 0;

  useEffect(() => {
    if (!suggestOpen && !modeOpen) return;
    function onPointer(e: MouseEvent) {
      const t = e.target as Node;
      if (modeMenuRef.current?.contains(t)) return;
      if (suggestOpen && composerRef.current?.contains(t)) {
        setModeOpen(false);
        return;
      }
      setShowSuggest(false);
      setModeOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [suggestOpen, modeOpen]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setFeedback(null);
    setShowSuggest(false);
    try {
      const res = await client.composer(value, mode);
      setResult(res);
      if (res.intent === "search" || res.intent === "refuse") {
        setLastQuery(value);
        return;
      }
      if (res.intent === "batch" || (res.items && res.items.length > 1)) {
        setText("");
        router.push("/archive?status=queued");
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
      const maybeUrl = text.trim();
      const sourceUrl =
        /^https?:\/\//i.test(maybeUrl) || /instagram\.com|youtu\.be|youtube\.com|tiktok\.com/i.test(maybeUrl)
          ? maybeUrl
          : undefined;
      const res = await client.upload(file, kind, sourceUrl);
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

  async function submitFeedback(helpful: boolean) {
    if (!result || !lastQuery || feedbackBusy) return;
    setFeedbackBusy(true);
    try {
      await client.sendSearchFeedback({
        query: lastQuery,
        answer: result.answer,
        source_ids: (result.sources || []).map((s) => s.id),
        helpful,
      });
      setFeedback(helpful ? "up" : "down");
    } catch {
      /* non-fatal */
    } finally {
      setFeedbackBusy(false);
    }
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
          <BrandMark size={72} className="mx-auto mb-5 rounded-[1.25rem] shadow-[0_12px_40px_rgba(0,0,0,0.35)]" priority />
          <h1
            className="home-hero-title text-[clamp(2.8rem,10vw,4.75rem)] leading-[0.92] tracking-[-0.04em]"
            style={{ fontFamily: "var(--font-display)" }}
          >
            Chronicum
          </h1>
          <p className="home-hero-sub mx-auto mt-4 max-w-md text-[var(--muted)]">
            Спросить базу, сохранить знание, задачу или правило — режим меняет структуру карточки.
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
          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
            <span>Ответ помог?</span>
            <button
              type="button"
              disabled={feedbackBusy}
              onClick={() => submitFeedback(true)}
              aria-pressed={feedback === "up"}
              className={`rounded-full border px-3 py-1 transition ${
                feedback === "up"
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                  : "border-[var(--line)] hover:border-[var(--accent)] hover:text-[var(--ink)]"
              }`}
            >
              Полезно
            </button>
            <button
              type="button"
              disabled={feedbackBusy}
              onClick={() => submitFeedback(false)}
              aria-pressed={feedback === "down"}
              className={`rounded-full border px-3 py-1 transition ${
                feedback === "down"
                  ? "border-[var(--warn)] text-[var(--warn)]"
                  : "border-[var(--line)] hover:border-[var(--warn)] hover:text-[var(--ink)]"
              }`}
            >
              Мимо
            </button>
            {feedback && (
              <span className="text-[var(--accent)]">Спасибо, сохранено</span>
            )}
          </div>
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
            className="composer-attach mb-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-[var(--line)] text-lg text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
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

          <div
            ref={modeMenuRef}
            className={`composer-mode-wrap relative mb-1 shrink-0 ${modeOpen ? "is-open" : ""}`}
          >
            <button
              type="button"
              className="composer-mode-trigger"
              aria-haspopup="listbox"
              aria-expanded={modeOpen}
              aria-label={`Режим: ${activeMode.label}`}
              title={activeMode.hint}
              onClick={() => {
                setModeOpen((o) => !o);
                setShowSuggest(false);
              }}
            >
              <span className="composer-mode-trigger-label">{activeMode.label}</span>
              <svg
                className="composer-mode-caret"
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                aria-hidden
              >
                <path
                  d="M2.5 4.25 6 7.75l3.5-3.5"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            {modeOpen && (
              <ul className="composer-mode-menu" role="listbox" aria-label="Режим ввода">
                {MODES.map((m) => {
                  const selected = mode === m.id;
                  return (
                    <li key={m.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={selected}
                        className={`composer-mode-option ${selected ? "is-active" : ""}`}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => {
                          setMode(m.id);
                          setModeOpen(false);
                          textRef.current?.focus();
                        }}
                      >
                        <span className="composer-mode-option-text">
                          <span className="composer-mode-option-label">{m.label}</span>
                          <span className="composer-mode-option-hint">{m.hint}</span>
                        </span>
                        {selected && (
                          <svg
                            className="composer-mode-check"
                            width="14"
                            height="14"
                            viewBox="0 0 14 14"
                            fill="none"
                            aria-hidden
                          >
                            <path
                              d="M2.5 7.2 5.6 10.2 11.5 3.8"
                              stroke="currentColor"
                              strokeWidth="1.7"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <textarea
            ref={textRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setShowSuggest(true);
              setModeOpen(false);
              if (result?.intent === "refuse") setResult(null);
            }}
            rows={1}
            enterKeyHint="send"
            placeholder={composerPlaceholder}
            className="composer-input max-h-40 min-h-[44px] w-full resize-none overflow-hidden bg-transparent py-2.5 outline-none placeholder:text-[var(--muted)]"
            onFocus={() => {
              setShowSuggest(true);
              setModeOpen(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setShowSuggest(false);
                setModeOpen(false);
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
            className="composer-send mb-1 flex h-11 min-w-11 shrink-0 items-center justify-center rounded-full border border-[var(--accent)] bg-[var(--accent)] px-3.5 text-sm text-[var(--btn-fg)] disabled:opacity-40"
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
            {activeMode.hint} · Enter — отправить · Shift+Enter — новая строка
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
