"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { CodeBlocksPanel } from "@/components/CodeBlocksPanel";
import { LinksPanel } from "@/components/LinksPanel";
import { MaterialListen } from "@/components/MaterialListen";
import { MaterialProcessing } from "@/components/MaterialProcessing";
import { MomentsGallery } from "@/components/MomentsGallery";
import { RichText } from "@/components/RichText";
import { TranscriptView } from "@/components/TranscriptView";
import { StatusPill, cleanMetaPart, formatShortDate } from "@/components/MaterialRow";
import {
  client,
  normalizeCodeBlocks,
  normalizeMaterialLinks,
  type MaterialDetail,
} from "@/lib/api";

const SOURCE_LABEL: Record<string, string> = {
  youtube: "YouTube",
  instagram: "Instagram",
  video: "Видео",
  article: "Статья",
  note: "Заметка",
  pdf: "PDF",
  voice: "Голос",
  file: "Файл",
};

type SlideId =
  | "overview"
  | "ideas"
  | "takeaways"
  | "evaluation"
  | "opinion"
  | "explain"
  | "moments"
  | "raw";

type Slide = { id: SlideId; label: string };

function digestLines(text: string): string[] {
  return text
    .split(/\n+/)
    .map((l) => l.replace(/^[-•*]\s*/, "").trim())
    .filter(Boolean);
}

export default function MaterialPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;
  const [m, setM] = useState<MaterialDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [score, setScore] = useState(5);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [scoreFlash, setScoreFlash] = useState(false);
  const [slide, setSlide] = useState(0);
  const [dir, setDir] = useState<1 | -1>(1);
  const [showScore, setShowScore] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const [touchX, setTouchX] = useState<number | null>(null);
  const [touchY, setTouchY] = useState<number | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  async function load() {
    try {
      const data = await client.getMaterial(id);
      setM(data);
      if (data.usefulness_score) setScore(data.usefulness_score);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  useEffect(() => {
    load();
    const busy = m && ["queued", "processing"].includes(m.processing_status);
    const t = setInterval(load, busy ? 2000 : 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, m?.processing_status]);

  const slides: Slide[] = useMemo(() => {
    if (!m) return [{ id: "overview", label: "Обзор" }];
    // Tabs only after the card is ready to read
    if (["queued", "processing"].includes(m.processing_status)) {
      return [{ id: "overview", label: "Обзор" }];
    }
    const list: Slide[] = [{ id: "overview", label: "Обзор" }];
    if (m.key_ideas?.length) list.push({ id: "ideas", label: "Идеи" });
    if (m.practical_takeaways?.length) list.push({ id: "takeaways", label: "Выводы" });
    if (normalizeCodeBlocks(m.code_blocks).length) list.push({ id: "code", label: "Код" });
    if (normalizeMaterialLinks(m.links).length) list.push({ id: "links", label: "Ссылки" });
    if (m.objective_evaluation) list.push({ id: "evaluation", label: "Оценка" });
    if (m.ai_opinion) list.push({ id: "opinion", label: "Мнение" });
    if (m.detailed_explanation) list.push({ id: "explain", label: "Подробно" });
    if ((m.key_moments?.length ?? 0) > 0) {
      list.push({ id: "moments", label: "Кадры" });
    }
    if (m.raw_text) list.push({ id: "raw", label: "Текст" });
    return list;
  }, [m]);

  useEffect(() => {
    setSlide((i) => Math.min(i, Math.max(0, slides.length - 1)));
  }, [slides.length]);

  useEffect(() => {
    tabRefs.current[slide]?.scrollIntoView({
      behavior: "smooth",
      inline: "center",
      block: "nearest",
    });
  }, [slide]);

  const go = useCallback(
    (next: number) => {
      setSlide((cur) => {
        const clamped = Math.max(0, Math.min(slides.length - 1, next));
        if (clamped === cur) return cur;
        setDir(clamped > cur ? 1 : -1);
        setShowScore(false);
        setShowMore(false);
        return clamped;
      });
    },
    [slides.length]
  );

  const onKeyNav = useCallback(
    (e: KeyboardEvent) => {
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
      if (e.key === "Escape") {
        if (showScore || showMore) {
          e.preventDefault();
          setShowScore(false);
          setShowMore(false);
        }
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        go(slide + 1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        go(slide - 1);
      }
    },
    [go, slide, showMore, showScore]
  );

  useEffect(() => {
    window.addEventListener("keydown", onKeyNav);
    return () => window.removeEventListener("keydown", onKeyNav);
  }, [onKeyNav]);

  async function run(action: string, fn: () => Promise<MaterialDetail>) {
    setBusy(action);
    setActionErr(null);
    setShowMore(false);
    try {
      const updated = await fn();
      setM(updated);
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <div className="archive-empty animate-fade">
        <p className="text-[var(--warn)]">{error}</p>
        <Link href="/archive" className="card-btn card-btn-accent mt-5">
          ← В архив
        </Link>
      </div>
    );
  }

  if (!m) {
    return (
      <div className="deck-page animate-fade">
        <div className="deck-stage">
          <div className="w-full max-w-xl space-y-4">
            <div className="archive-skeleton h-8 w-2/3" />
            <div className="archive-skeleton h-28 w-full rounded-[var(--radius-xl)]" />
            <div className="archive-skeleton h-10 w-40" />
          </div>
        </div>
      </div>
    );
  }

  const isVideo = ["youtube", "instagram", "video"].includes(m.source_type);
  const digest = m.digest || m.summary;
  const evalObj = m.objective_evaluation;
  const source = SOURCE_LABEL[m.source_type] || m.source_type;
  const category = cleanMetaPart(m.category);
  const isProcessing = ["queued", "processing"].includes(m.processing_status);
  const cardReady = !isProcessing && m.processing_status !== "failed";

  if (!cardReady) {
    return (
      <div className="deck-page animate-fade" data-has-nav="false">
        <div className="deck-frame">
          <header className="deck-chrome">
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              <Link href="/archive" className="deck-back">
                ← Архив
              </Link>
              <StatusPill status={m.processing_status} />
            </div>
          </header>
          <div className="deck-stage deck-stage--proc">
            <MaterialProcessing
              title={m.title}
              sourceLabel={source}
              status={m.processing_status}
              stage={m.processing_stage}
              retryBusy={busy === "retry"}
              onRetry={
                m.processing_status === "failed"
                  ? () => void run("retry", () => client.retry(m.id).then(() => client.getMaterial(m.id)))
                  : undefined
              }
            />
          </div>
        </div>
      </div>
    );
  }

  const scoreValue = m.usefulness_score;
  const dateLabel = formatShortDate(m.created_at);
  const current = slides[slide] || slides[0];
  const nextSlide = slides[slide + 1];
  const prevSlide = slides[slide - 1];
  const lines = digest ? digestLines(digest) : [];
  const digestAsList = lines.length > 1 && lines.every((l) => l.length < 140);
  const reason = m.usefulness_reason || "";
  const hasOverviewBody = Boolean(digest || (m.main_idea && m.main_idea !== digest));

  const speakParts: string[] = [];
  switch (current.id) {
    case "overview": {
      // Important card body first — title alone is useless for TTS
      if (m.main_idea) speakParts.push(m.main_idea);
      if (digest) speakParts.push(digest);
      if (reason) speakParts.push(reason);
      if (!speakParts.length && m.title) speakParts.push(m.title);
      break;
    }
    case "ideas":
      (m.key_ideas || []).forEach((idea, i) => {
        speakParts.push(`Идея ${i + 1}. ${idea}`);
      });
      break;
    case "takeaways":
      (m.practical_takeaways || []).forEach((item, i) => {
        speakParts.push(`Вывод ${i + 1}. ${item}`);
      });
      break;
    case "code":
      // Code is for reading/copying — no TTS
      break;
    case "links":
      normalizeMaterialLinks(m.links).forEach((l, i) => {
        speakParts.push(`${l.title || `Ссылка ${i + 1}`}: ${l.url}`);
      });
      break;
    case "evaluation": {
      if (evalObj?.verdict) speakParts.push(String(evalObj.verdict));
      if (evalObj?.best_for) speakParts.push(`Кому полезно: ${evalObj.best_for}`);
      if (evalObj?.pros?.length) speakParts.push(`Плюсы: ${evalObj.pros.join(". ")}`);
      if (evalObj?.cons?.length) speakParts.push(`Минусы: ${evalObj.cons.join(". ")}`);
      break;
    }
    case "opinion":
      if (m.ai_opinion) speakParts.push(m.ai_opinion);
      break;
    case "explain":
      if (m.detailed_explanation) speakParts.push(m.detailed_explanation);
      break;
    case "raw":
      // Prefer speech without flooding — drop timestamps via MaterialListen stripper
      if (m.raw_text) speakParts.push(m.raw_text.slice(0, 8000));
      break;
    case "moments":
      // Frames are visual — no TTS
      break;
    default:
      break;
  }
  const speakText = speakParts.filter(Boolean).join("\n\n");
  const speakLabel = current.label;
  const speakKey = `${m.id}:${current.id}`;

  async function pickScore(n: number) {
    if (!m) return;
    setScore(n);
    setBusy("score");
    setActionErr(null);
    try {
      const updated = await client.updateScore(m.id, n, m.usefulness_reason || undefined);
      setM(updated);
      setScoreFlash(true);
      window.setTimeout(() => {
        setScoreFlash(false);
        setShowScore(false);
      }, 650);
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : "Не удалось сохранить оценку");
    } finally {
      setBusy(null);
    }
  }

  async function deleteMaterial() {
    if (!m) return;
    if (!window.confirm("Удалить материал безвозвратно?")) return;
    setBusy("delete");
    try {
      await client.deleteMaterial(m.id);
      router.push("/archive");
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : "Не удалось удалить");
      setBusy(null);
    }
  }

  const stageBody = (() => {
    switch (current.id) {
      case "overview":
        return (
          <div className="deck-overview">
            <header className="deck-overview-head">
              <h1 className="deck-title" style={{ fontFamily: "var(--font-display)" }}>
                {m.title || "Без названия"}
              </h1>
              <div className="deck-meta">
                <span className="deck-meta-chip">{source}</span>
                {category && <span className="deck-meta-chip">{category}</span>}
                {dateLabel && <span className="deck-meta-chip">{dateLabel}</span>}
                {m.source_url && (
                  <a
                    href={m.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="deck-meta-chip link"
                  >
                    источник ↗
                  </a>
                )}
              </div>
              {!!m.tags.length && (
                <div className="deck-tags">
                  {m.tags.map((t) => (
                    <span key={t} className="deck-tag">
                      #{t}
                    </span>
                  ))}
                </div>
              )}
            </header>

            <div className="deck-overview-body">
              <div className="deck-overview-copy">
                {m.main_idea && m.main_idea !== digest && (
                  <p className="deck-lead">{m.main_idea}</p>
                )}

                {digest && (
                  <div className="deck-digest">
                    <p className="deck-kicker">Выжимка</p>
                    {digestAsList ? (
                      <ul className="deck-bullets">
                        {lines.map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    ) : (
                      <RichText text={digest} className="deck-prose" />
                    )}
                  </div>
                )}

                {!hasOverviewBody && (
                  <div className="deck-waiting">
                    <p className="deck-kicker">{isProcessing ? "В работе" : "Пока пусто"}</p>
                    <p>
                      {isProcessing
                        ? "Материал обрабатывается — выжимка появится здесь."
                        : "Запустите обогащение или полную обработку, чтобы заполнить карточку."}
                    </p>
                  </div>
                )}
              </div>

              <aside className="deck-overview-rail" aria-label="Оценка и прослушивание">
                <div className="deck-score-row">
                  <button
                    type="button"
                    className="deck-score-hit"
                    aria-expanded={showScore}
                    aria-label="Изменить оценку полезности"
                    onClick={() => {
                      setShowScore((v) => !v);
                      setShowMore(false);
                    }}
                  >
                    <span className="deck-score-inline" style={{ fontFamily: "var(--font-display)" }}>
                      <span className="text-[var(--accent)]">{scoreValue ?? "—"}</span>
                      <span className="text-[var(--muted)]">/10</span>
                    </span>
                    <span className="deck-score-cue">
                      {scoreFlash ? "Сохранено" : showScore ? "Выберите" : "Оценка"}
                    </span>
                  </button>
                </div>

                {reason && !showScore && (
                  <div className="deck-reason-wrap">
                    <p className="deck-reason">{reason}</p>
                  </div>
                )}

                {showScore && (
                  <div className="deck-score-panel" role="group" aria-label="Оценка от 1 до 10">
                    <p className="deck-panel-label">Нажмите, чтобы сохранить</p>
                    <div className="deck-score-grid">
                      {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                        <button
                          key={n}
                          type="button"
                          className="deck-score-chip"
                          data-active={score === n}
                          disabled={busy === "score"}
                          onClick={() => pickScore(n)}
                        >
                          {n}
                        </button>
                      ))}
                    </div>
                    {busy === "score" && <p className="deck-score-saving">Сохраняю…</p>}
                  </div>
                )}

                <div className="deck-overview-listen">
                  <MaterialListen
                    materialId={m.id}
                    hasAudio={Boolean(m.has_audio)}
                    durationSeconds={m.duration_seconds}
                    speakText={speakText}
                    speakLabel={speakLabel}
                    speakKey={speakKey}
                  />
                </div>
              </aside>
            </div>

            <div className="deck-actions">
              <button
                type="button"
                disabled={!!busy}
                className="deck-act primary"
                onClick={() => run("opinion", () => client.opinion(m.id))}
              >
                {busy === "opinion" ? "Думаю…" : "Мнение ИИ"}
              </button>
              <button
                type="button"
                className="deck-act"
                data-open={showMore}
                onClick={() => {
                  setShowMore((v) => !v);
                  setShowScore(false);
                }}
              >
                {showMore ? "Скрыть" : "Ещё"}
              </button>
            </div>

            {showMore && (
              <div className="deck-menu" role="menu" aria-label="Дополнительные действия">
                <section className="deck-menu-section">
                  <p className="deck-panel-label">Обогатить</p>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={!!busy}
                    className="deck-menu-item"
                    onClick={() => run("explain", () => client.explain(m.id))}
                  >
                    <span>
                      <span className="deck-menu-title">Подробнее</span>
                      <span className="deck-menu-desc">Развернуть объяснение</span>
                    </span>
                    <span className="deck-menu-trail">{busy === "explain" ? "…" : "›"}</span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={!!busy}
                    className="deck-menu-item"
                    onClick={() => run("digest", () => client.digest(m.id))}
                  >
                    <span>
                      <span className="deck-menu-title">Обновить выжимку</span>
                      <span className="deck-menu-desc">Пересобрать краткий разбор</span>
                    </span>
                    <span className="deck-menu-trail">{busy === "digest" ? "…" : "›"}</span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    disabled={!!busy}
                    className="deck-menu-item"
                    onClick={() => run("evaluate", () => client.evaluate(m.id))}
                  >
                    <span>
                      <span className="deck-menu-title">Объективная оценка</span>
                      <span className="deck-menu-desc">Качество материала</span>
                    </span>
                    <span className="deck-menu-trail">{busy === "evaluate" ? "…" : "›"}</span>
                  </button>
                  {isVideo && (
                    <button
                      type="button"
                      role="menuitem"
                      disabled={!!busy}
                      className="deck-menu-item"
                      onClick={() => run("moments", () => client.moments(m.id))}
                    >
                      <span>
                        <span className="deck-menu-title">Ключевые кадры</span>
                        <span className="deck-menu-desc">Скриншоты из видео</span>
                      </span>
                      <span className="deck-menu-trail">{busy === "moments" ? "…" : "›"}</span>
                    </button>
                  )}
                </section>

                <section className="deck-menu-section">
                  <p className="deck-panel-label">Обработка</p>
                  <button
                    type="button"
                    role="menuitem"
                    className="deck-menu-item"
                    onClick={async () => {
                      await client.retry(m.id);
                      await load();
                      setShowMore(false);
                    }}
                  >
                    <span>
                      <span className="deck-menu-title">Повторить</span>
                      <span className="deck-menu-desc">Запустить разбор заново</span>
                    </span>
                    <span className="deck-menu-trail">›</span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="deck-menu-item"
                    onClick={async () => {
                      await client.processFull(m.id);
                      await load();
                      setShowMore(false);
                    }}
                  >
                    <span>
                      <span className="deck-menu-title">Полная обработка</span>
                      <span className="deck-menu-desc">Глубокий проход по материалу</span>
                    </span>
                    <span className="deck-menu-trail">›</span>
                  </button>
                </section>

                <button
                  type="button"
                  role="menuitem"
                  disabled={!!busy}
                  className="deck-menu-delete"
                  onClick={deleteMaterial}
                >
                  {busy === "delete" ? "Удаляю…" : "Удалить материал"}
                </button>
              </div>
            )}
            {actionErr && <p className="deck-error">{actionErr}</p>}
          </div>
        );

      case "ideas":
        return (
          <div className="deck-slide-inner">
            <header className="deck-slide-head">
              <p className="deck-kicker">Ключевые идеи</p>
              <span className="deck-slide-count">{m.key_ideas.length}</span>
            </header>
            <ol className="deck-ideas">
              {m.key_ideas.map((x, i) => (
                <li key={x} style={{ animationDelay: `${i * 45}ms` }}>
                  <span style={{ fontFamily: "var(--font-display)" }}>{String(i + 1).padStart(2, "0")}</span>
                  <p>{x}</p>
                </li>
              ))}
            </ol>
          </div>
        );

      case "takeaways":
        return (
          <div className="deck-slide-inner">
            <header className="deck-slide-head">
              <p className="deck-kicker">Практические выводы</p>
              <span className="deck-slide-count">{m.practical_takeaways.length}</span>
            </header>
            <ul className="deck-takeaways">
              {m.practical_takeaways.map((x, i) => (
                <li key={x} style={{ animationDelay: `${i * 45}ms` }}>
                  {x}
                </li>
              ))}
            </ul>
          </div>
        );

      case "code": {
        const blocks = normalizeCodeBlocks(m.code_blocks);
        return (
          <div className="deck-slide-inner deck-slide-wide">
            <header className="deck-slide-head">
              <p className="deck-kicker">Код</p>
              <span className="deck-slide-count">{blocks.length}</span>
            </header>
            <CodeBlocksPanel blocks={blocks} />
          </div>
        );
      }

      case "links": {
        const links = normalizeMaterialLinks(m.links);
        return (
          <div className="deck-slide-inner">
            <header className="deck-slide-head">
              <p className="deck-kicker">Ссылки</p>
              <span className="deck-slide-count">{links.length}</span>
            </header>
            <LinksPanel links={links} />
          </div>
        );
      }

      case "evaluation":
        return (
          <div className="deck-slide-inner">
            <p className="deck-kicker">Объективная оценка</p>
            {evalObj?.verdict && (
              <p className="deck-verdict" style={{ fontFamily: "var(--font-display)" }}>
                {evalObj.verdict}
              </p>
            )}
            {evalObj?.best_for && (
              <p className="deck-best-for">
                <span>Кому</span> {evalObj.best_for}
              </p>
            )}
            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              {!!evalObj?.pros?.length && (
                <div className="deck-eval">
                  <p className="card-label mb-2">Плюсы</p>
                  <ul className="space-y-2 text-sm">
                    {evalObj.pros.map((x) => (
                      <li key={x} className="flex gap-2">
                        <span className="text-[var(--accent)]">+</span>
                        <span>{x}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {!!evalObj?.cons?.length && (
                <div className="deck-eval">
                  <p className="card-label mb-2">Минусы</p>
                  <ul className="space-y-2 text-sm">
                    {evalObj.cons.map((x) => (
                      <li key={x} className="flex gap-2">
                        <span className="text-[var(--warn)]">−</span>
                        <span>{x}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        );

      case "opinion":
        return (
          <div className="deck-slide-inner">
            <p className="deck-kicker">Мнение ИИ</p>
            <RichText text={m.ai_opinion} className="deck-prose" />
          </div>
        );

      case "explain":
        return (
          <div className="deck-slide-inner">
            <p className="deck-kicker">Подробное объяснение</p>
            <RichText
              text={m.detailed_explanation}
              className="deck-prose text-[color-mix(in_srgb,var(--ink)_90%,var(--muted))]"
            />
          </div>
        );

      case "moments":
        return (
          <div className="deck-slide-inner deck-slide-wide">
            <p className="deck-kicker">Ключевые кадры</p>
            {(m.key_moments?.length ?? 0) > 0 ? (
              <MomentsGallery materialId={m.id} moments={m.key_moments || []} embedded />
            ) : (
              <div className="deck-waiting">
                <p>
                  {isProcessing
                    ? "Кадры появятся после обработки видео."
                    : "Пока нет кадров. Соберите их через «Ещё → Ключевые кадры» в обзоре."}
                </p>
              </div>
            )}
          </div>
        );

      case "raw":
        return (
          <div className="deck-slide-inner">
            <p className="deck-kicker">Полный текст</p>
            <TranscriptView text={m.raw_text || ""} />
          </div>
        );

      default:
        return null;
    }
  })();

  return (
    <div
      className="deck-page animate-fade"
      data-has-nav={slides.length > 1 ? "true" : "false"}
      onTouchStart={(e) => {
        const t = e.changedTouches[0];
        setTouchX(t?.clientX ?? null);
        setTouchY(t?.clientY ?? null);
      }}
      onTouchEnd={(e) => {
        if (touchX == null || touchY == null) return;
        const t = e.changedTouches[0];
        if (!t) return;
        const dx = t.clientX - touchX;
        const dy = t.clientY - touchY;
        setTouchX(null);
        setTouchY(null);
        if (slides.length < 2) return;
        if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
        const el = e.target as HTMLElement | null;
        if (el?.closest?.(".deck-raw, .deck-raw-md, .transcript-view, .moments-gallery, .material-listen, .deck-score-panel, input, textarea, select, button, a")) {
          return;
        }
        go(slide + (dx < 0 ? 1 : -1));
      }}
    >
      <div className="deck-frame">
      <header className="deck-chrome">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <Link href="/archive" className="deck-back">
            ← Архив
          </Link>
          <StatusPill status={m.processing_status} />
          {(isProcessing || m.processing_status === "awaiting_full" || m.error_message) && (
            <span
              className={`deck-status-note ${
                m.error_message || m.processing_status === "failed" ? "is-warn" : ""
              }`}
            >
              {m.error_message
                ? "ошибка"
                : m.processing_status === "awaiting_full"
                  ? "ждёт полной обработки"
                  : "обрабатывается…"}
            </span>
          )}
        </div>
      </header>

      {slides.length > 1 && (
        <div className="deck-tabs-wrap">
          <div className="deck-tabs" role="tablist" aria-label="Разделы карточки">
            {slides.map((s, i) => (
              <button
                key={s.id}
                type="button"
                role="tab"
                aria-selected={i === slide}
                className="deck-tab"
                data-active={i === slide}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                onClick={() => go(i)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {current.id !== "overview" && (
        <div className="material-listen-dock material-listen-dock--bar">
          <MaterialListen
            materialId={m.id}
            hasAudio={Boolean(m.has_audio)}
            durationSeconds={m.duration_seconds}
            speakText={speakText}
            speakLabel={speakLabel}
            speakKey={speakKey}
          />
        </div>
      )}

      <div
        key={current.id}
        className="deck-stage"
        data-dir={dir}
        role="tabpanel"
        aria-label={current.label}
      >
        {stageBody}
      </div>
      </div>

      {slides.length > 1 && (
        <nav className="deck-nav" aria-label="Навигация по разделам">
          <button
            type="button"
            className="deck-nav-btn"
            disabled={!prevSlide}
            onClick={() => go(slide - 1)}
          >
            {prevSlide ? `← ${prevSlide.label}` : "←"}
          </button>
          <span className="deck-nav-pos tabular-nums">
            {slide + 1}
            <span className="opacity-40"> / </span>
            {slides.length}
          </span>
          <button
            type="button"
            className="deck-nav-btn"
            data-primary={Boolean(nextSlide)}
            disabled={!nextSlide}
            onClick={() => go(slide + 1)}
          >
            {nextSlide ? `${nextSlide.label} →` : "→"}
          </button>
        </nav>
      )}
    </div>
  );
}
