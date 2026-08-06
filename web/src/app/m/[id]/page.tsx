"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MomentsGallery } from "@/components/MomentsGallery";
import { StatusPill } from "@/components/MaterialRow";
import { client, type MaterialDetail } from "@/lib/api";

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

export default function MaterialPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;
  const [m, setM] = useState<MaterialDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [score, setScore] = useState(5);
  const [reasonEdit, setReasonEdit] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [scoreSaved, setScoreSaved] = useState(false);

  async function load() {
    try {
      const data = await client.getMaterial(id);
      setM(data);
      if (data.usefulness_score) setScore(data.usefulness_score);
      setReasonEdit(data.usefulness_reason || "");
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    }
  }

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function run(action: string, fn: () => Promise<MaterialDetail>) {
    setBusy(action);
    setActionErr(null);
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
      <div className="grid gap-8 lg:grid-cols-[1.4fr_0.75fr] animate-fade">
        <div className="space-y-4">
          <div className="archive-skeleton h-4 w-40" />
          <div className="archive-skeleton h-10 w-3/4 max-w-xl" />
          <div className="archive-skeleton h-40 w-full" />
          <div className="archive-skeleton h-24 w-full" />
        </div>
        <div className="space-y-4">
          <div className="archive-skeleton h-44 w-full" />
          <div className="archive-skeleton h-32 w-full" />
        </div>
      </div>
    );
  }

  const isVideo = ["youtube", "instagram", "video"].includes(m.source_type);
  const digest = m.digest || m.summary;
  const evalObj = m.objective_evaluation;
  const source = SOURCE_LABEL[m.source_type] || m.source_type;
  const isProcessing = ["queued", "processing"].includes(m.processing_status);
  const scoreValue = m.usefulness_score;
  const scorePct = scoreValue != null ? Math.max(6, Math.min(100, scoreValue * 10)) : 0;
  const tech = [...(m.tools || []), ...(m.technologies || [])];

  return (
    <article className="grid gap-8 lg:grid-cols-[minmax(0,1.45fr)_minmax(16rem,0.75fr)] lg:gap-10">
      <div className="min-w-0 animate-rise">
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <Link href="/archive" className="card-btn py-1.5 text-xs">
            ← Архив
          </Link>
          <StatusPill status={m.processing_status} />
          <span className="archive-chip-meta">{source}</span>
          {m.category && <span className="archive-chip-meta">{m.category}</span>}
          {m.processing_stage && (
            <span className="text-[11px] tabular-nums text-[var(--muted)]">
              stage · {m.processing_stage}
            </span>
          )}
        </div>

        {(isProcessing || m.processing_status === "awaiting_full" || m.error_message) && (
          <div
            className={`mb-6 rounded-[var(--radius-md)] border px-4 py-3 text-sm ${
              m.error_message || m.processing_status === "failed"
                ? "border-[color-mix(in_srgb,var(--warn)_40%,var(--line))] text-[var(--warn)]"
                : "border-[color-mix(in_srgb,var(--accent)_35%,var(--line))] text-[var(--accent)]"
            }`}
          >
            {m.error_message
              ? m.error_message
              : m.processing_status === "awaiting_full"
                ? "Превью готово — полный разбор не запускался (score ниже порога). Можно запустить вручную в сайдбаре."
                : isProcessing
                  ? "Материал обрабатывается — карточка обновится автоматически."
                  : null}
          </div>
        )}

        <h1
          className="text-[clamp(1.85rem,3.8vw,2.85rem)] leading-[1.08] tracking-[-0.03em]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {m.title || "Без названия"}
        </h1>

        {m.source_url && (
          <a
            href={m.source_url}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-flex items-center gap-1 text-sm text-[var(--accent)] hover:underline"
          >
            Открыть источник
            <span aria-hidden>↗</span>
          </a>
        )}

        {digest && (
          <section className="archive-panel mt-8 p-5 md:p-6">
            <h2 className="card-label">Выжимка</h2>
            <p className="mt-3 whitespace-pre-wrap text-[1.05rem] leading-[1.7] md:text-lg">{digest}</p>
          </section>
        )}

        {m.main_idea && (
          <p className="mt-6 max-w-2xl border-l-2 border-[var(--accent)] pl-4 leading-relaxed text-[var(--muted)]">
            {m.main_idea}
          </p>
        )}

        {m.summary && m.digest && m.summary !== m.digest && (
          <p className="mt-6 max-w-2xl leading-relaxed text-[var(--muted)]">{m.summary}</p>
        )}

        <div className="mt-8 space-y-3">
          <p className="card-label">Действия</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={!!busy}
              className="card-btn card-btn-primary"
              onClick={() => run("opinion", () => client.opinion(m.id))}
            >
              {busy === "opinion" ? "…" : "Мнение ИИ"}
            </button>
            <button
              type="button"
              disabled={!!busy}
              className="card-btn card-btn-accent"
              onClick={() => run("explain", () => client.explain(m.id))}
            >
              {busy === "explain" ? "…" : "Подробнее"}
            </button>
            <button
              type="button"
              disabled={!!busy}
              className="card-btn"
              onClick={() => run("digest", () => client.digest(m.id))}
            >
              {busy === "digest" ? "…" : "Обновить выжимку"}
            </button>
            <button
              type="button"
              disabled={!!busy}
              className="card-btn"
              onClick={() => run("evaluate", () => client.evaluate(m.id))}
            >
              {busy === "evaluate" ? "…" : "Объективная оценка"}
            </button>
            {isVideo && (
              <button
                type="button"
                disabled={!!busy}
                className="card-btn"
                onClick={() => run("moments", () => client.moments(m.id))}
              >
                {busy === "moments" ? "…" : "Ключевые кадры"}
              </button>
            )}
            <button
              type="button"
              disabled={!!busy}
              className="card-btn card-btn-danger"
              onClick={async () => {
                if (!window.confirm("Удалить материал безвозвратно?")) return;
                setBusy("delete");
                setActionErr(null);
                try {
                  await client.deleteMaterial(m.id);
                  router.push("/archive");
                } catch (e) {
                  setActionErr(e instanceof Error ? e.message : "Не удалось удалить");
                  setBusy(null);
                }
              }}
            >
              {busy === "delete" ? "…" : "Удалить"}
            </button>
          </div>
          {actionErr && (
            <p className="border-l-2 border-[var(--warn)] pl-3 text-sm text-[var(--warn)]">{actionErr}</p>
          )}
        </div>

        {m.detailed_explanation && (
          <section className="mt-10">
            <h2 className="card-label mb-3">Подробное объяснение</h2>
            <div className="whitespace-pre-wrap leading-relaxed">{m.detailed_explanation}</div>
          </section>
        )}

        {evalObj && (
          <section className="archive-panel mt-10 p-5">
            <h2 className="card-label mb-3">Объективная оценка</h2>
            {evalObj.verdict && (
              <p className="text-xl leading-snug" style={{ fontFamily: "var(--font-display)" }}>
                {evalObj.verdict}
              </p>
            )}
            {evalObj.best_for && (
              <p className="mt-2 text-sm text-[var(--muted)]">Кому: {evalObj.best_for}</p>
            )}
            {!!evalObj.pros?.length && (
              <div className="mt-5">
                <p className="card-label">Плюсы</p>
                <ul className="mt-2 space-y-1.5 text-sm">
                  {evalObj.pros.map((x) => (
                    <li key={x} className="flex gap-2">
                      <span className="text-[var(--accent)]">+</span>
                      <span>{x}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!!evalObj.cons?.length && (
              <div className="mt-4">
                <p className="card-label">Минусы</p>
                <ul className="mt-2 space-y-1.5 text-sm">
                  {evalObj.cons.map((x) => (
                    <li key={x} className="flex gap-2">
                      <span className="text-[var(--warn)]">−</span>
                      <span>{x}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!!evalObj.risks?.length && (
              <div className="mt-4">
                <p className="card-label">Риски</p>
                <ul className="mt-2 space-y-1.5 text-sm text-[var(--muted)]">
                  {evalObj.risks.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {m.ai_opinion && (
          <section className="mt-10">
            <h2 className="card-label mb-3">Мнение ИИ</h2>
            <div className="whitespace-pre-wrap leading-relaxed">{m.ai_opinion}</div>
            {!!m.ai_opinion_sources?.length && (
              <ul className="mt-4 space-y-1.5 text-sm text-[var(--muted)]">
                {m.ai_opinion_sources.map((s) => (
                  <li key={s} className="truncate">
                    {s}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {!!m.key_moments?.length && <MomentsGallery materialId={m.id} moments={m.key_moments} />}

        {m.key_ideas?.length > 0 && (
          <section className="mt-10">
            <h2 className="card-label mb-3">Ключевые идеи</h2>
            <ul className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] divide-y divide-[var(--line)]">
              {m.key_ideas.map((x, i) => (
                <li key={x} className="flex gap-3 px-4 py-3">
                  <span className="tabular-nums text-[11px] text-[var(--muted)]">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span>{x}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {m.practical_takeaways?.length > 0 && (
          <section className="mt-8">
            <h2 className="card-label mb-3">Практические выводы</h2>
            <ul className="space-y-2.5">
              {m.practical_takeaways.map((x) => (
                <li key={x} className="flex gap-2.5 text-[var(--muted)]">
                  <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-[var(--accent)]" />
                  <span>{x}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {m.raw_text && (
          <details className="archive-panel mt-10 p-4">
            <summary className="cursor-pointer text-sm text-[var(--muted)] hover:text-[var(--ink)]">
              Полный текст / транскрипт
            </summary>
            <pre className="mt-4 max-h-[420px] overflow-auto whitespace-pre-wrap rounded-[var(--radius-md)] border border-[var(--line)] bg-[var(--bg-deep)] p-4 text-sm leading-relaxed">
              {m.raw_text}
            </pre>
          </details>
        )}
      </div>

      <aside className="space-y-4 animate-fade lg:sticky lg:top-8 lg:self-start">
        <div className="archive-panel p-5">
          <p className="card-label">Полезность</p>
          <div className="mt-4 flex items-center gap-4">
            <div className="card-score relative shrink-0">
              <span className="card-score-ring" />
              <div className="relative z-[1] text-center">
                <p
                  className="text-3xl tabular-nums leading-none"
                  style={{ fontFamily: "var(--font-display)" }}
                >
                  {scoreValue ?? "—"}
                </p>
                <p className="mt-1 text-[10px] uppercase tracking-[0.14em] text-[var(--muted)]">/ 10</p>
              </div>
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm leading-relaxed text-[var(--muted)]">
                {m.usefulness_reason || "Оценка появится после разбора"}
              </p>
              {scoreValue != null && (
                <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-[var(--line)]">
                  <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${scorePct}%` }} />
                </div>
              )}
            </div>
          </div>

          <div className="mt-5 flex items-center gap-2">
            <input
              type="range"
              min={1}
              max={10}
              value={score}
              onChange={(e) => {
                setScore(Number(e.target.value));
                setScoreSaved(false);
              }}
              className="w-full accent-[var(--accent)]"
              aria-label="Оценка полезности"
            />
            <span className="w-6 tabular-nums text-sm text-[var(--accent)]">{score}</span>
            <button
              type="button"
              className="card-btn card-btn-primary px-3 py-1.5 text-xs"
              onClick={async () => {
                const updated = await client.updateScore(m.id, score, reasonEdit.trim() || undefined);
                setM(updated);
                setScoreSaved(true);
              }}
            >
              OK
            </button>
          </div>
          <textarea
            value={reasonEdit}
            onChange={(e) => {
              setReasonEdit(e.target.value);
              setScoreSaved(false);
            }}
            rows={2}
            placeholder="Почему такая оценка (опционально)"
            className="mt-3 w-full resize-y rounded-[var(--radius-md)] border border-[var(--line)] bg-[var(--bg-deep)] p-3 text-sm outline-none transition focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-soft)]"
          />
          {scoreSaved && <p className="mt-2 text-xs text-[var(--accent)]">Оценка сохранена</p>}
        </div>

        <div className="space-y-4 rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--surface)] p-5">
          <div>
            <p className="card-label">Категория</p>
            <p className="mt-1.5 text-sm">{m.category || "—"}</p>
          </div>
          <div>
            <p className="card-label">Теги</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {m.tags.length ? (
                m.tags.map((t) => (
                  <span key={t} className="archive-chip-meta">
                    #{t}
                  </span>
                ))
              ) : (
                <span className="text-sm text-[var(--muted)]">—</span>
              )}
            </div>
          </div>
          {tech.length > 0 && (
            <div>
              <p className="card-label">Инструменты / tech</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {tech.map((t) => (
                  <span key={t} className="archive-chip-meta">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--surface)] p-4">
          <p className="card-label mb-3">Пайплайн</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="card-btn"
              onClick={async () => {
                await client.retry(m.id);
                await load();
              }}
            >
              Retry
            </button>
            <button
              type="button"
              className={
                m.processing_status === "awaiting_full"
                  ? "card-btn card-btn-primary"
                  : "card-btn card-btn-accent"
              }
              onClick={async () => {
                await client.processFull(m.id);
                await load();
              }}
            >
              Полная обработка
            </button>
          </div>
        </div>

        <p className="truncate px-1 text-[10px] tabular-nums text-[var(--muted)]">id · {m.id}</p>
      </aside>
    </article>
  );
}
