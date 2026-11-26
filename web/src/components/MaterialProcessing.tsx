"use client";

import Link from "next/link";
import { useMemo } from "react";

const STEPS = [
  { id: "queue", label: "В очереди", hint: "Материал принят" },
  { id: "fetch", label: "Забираю источник", hint: "Скачиваю и готовлю медиа" },
  { id: "asr", label: "Слушаю / читаю", hint: "Транскрипт, OCR, текст" },
  { id: "card", label: "Собираю карточку", hint: "Выжимка, идеи, оценка" },
  { id: "moments", label: "Кадры", hint: "Ключевые моменты с экрана" },
] as const;

/** Map backend processing_stage (+ status) → active step index. No timer fakes. */
function stepIndex(status: string, stage: string | null | undefined): number {
  if (status === "queued") return 0;
  if (status !== "processing" && status !== "failed") return 0;
  const s = (stage || "").toLowerCase();
  if (s === "fetch") return 1;
  if (s === "asr") return 2;
  if (s === "card" || s === "preview" || s === "full") return 3;
  if (s === "moments") return 4;
  // processing but stage not yet written
  return 1;
}

export function MaterialProcessing({
  title,
  sourceLabel,
  status,
  stage,
  errorMessage,
  onRetry,
  retryBusy,
}: {
  title?: string | null;
  sourceLabel: string;
  status: string;
  stage?: string | null;
  errorMessage?: string | null;
  onRetry?: () => void;
  retryBusy?: boolean;
}) {
  const active = useMemo(() => stepIndex(status, stage), [status, stage]);
  const failed = status === "failed";
  const showMoments = active >= 4 || stage === "moments";
  const visibleSteps = showMoments ? STEPS : STEPS.slice(0, 4);

  const activeHint =
    visibleSteps[Math.min(active, visibleSteps.length - 1)]?.hint ||
    "Идёт обработка";
  const cleanError = (errorMessage || "").trim();

  return (
    <div
      className={`mat-proc${failed ? " mat-proc--failed" : ""}`}
      role={failed ? "alert" : "status"}
      aria-live="polite"
    >
      <div className="mat-proc-orb" aria-hidden>
        <span className="mat-proc-ring mat-proc-ring--a" />
        <span className="mat-proc-ring mat-proc-ring--b" />
        <span className="mat-proc-ring mat-proc-ring--c" />
        <span className="mat-proc-core">
          <span className="mat-proc-core-glow" />
          <span className="mat-proc-core-mark">{failed ? "!" : "◈"}</span>
        </span>
      </div>

      <p className="mat-proc-kicker">{failed ? "Ошибка обработки" : "Обработка"}</p>
      <h1 className="mat-proc-title" style={{ fontFamily: "var(--font-display)" }}>
        {failed ? "Не удалось разобрать" : visibleSteps[Math.min(active, visibleSteps.length - 1)]?.label || "Собираю карточку"}
      </h1>
      <p className="mat-proc-sub">
        {failed
          ? "Разбор остановился с ошибкой. Можно запустить обработку заново."
          : activeHint}
      </p>

      {failed && cleanError && (
        <p className="mat-proc-err" title={cleanError}>
          {cleanError}
        </p>
      )}

      <div className="mat-proc-meta">
        <span className="mat-proc-chip">{sourceLabel}</span>
        {(title || "").trim() && title !== "Без названия" && (
          <span className="mat-proc-chip mat-proc-chip--soft" title={title || undefined}>
            {title}
          </span>
        )}
      </div>

      {!failed && (
        <ol className="mat-proc-steps">
          {visibleSteps.map((s, i) => {
            const state = i < active ? "done" : i === active ? "active" : "todo";
            return (
              <li key={s.id} className="mat-proc-step" data-state={state}>
                <span className="mat-proc-step-dot" aria-hidden>
                  {state === "done" ? "✓" : state === "active" ? "" : i + 1}
                </span>
                <span className="mat-proc-step-copy">
                  <span className="mat-proc-step-label">{s.label}</span>
                  <span className="mat-proc-step-hint">{s.hint}</span>
                </span>
              </li>
            );
          })}
        </ol>
      )}

      <div className="mat-proc-actions">
        <Link href="/archive" className="mat-proc-btn">
          ← В архив
        </Link>
        {failed && onRetry && (
          <button
            type="button"
            className="mat-proc-btn mat-proc-btn--primary"
            disabled={retryBusy}
            onClick={onRetry}
          >
            {retryBusy ? "Запускаю…" : "Повторить"}
          </button>
        )}
      </div>
    </div>
  );
}
