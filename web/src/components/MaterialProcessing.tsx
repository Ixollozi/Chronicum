"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

const STEPS = [
  { id: "queue", label: "В очереди", hint: "Материал принят" },
  { id: "fetch", label: "Забираю источник", hint: "Скачиваю и готовлю медиа" },
  { id: "listen", label: "Слушаю / читаю", hint: "Транскрипт и текст" },
  { id: "think", label: "Разбираю смысл", hint: "Выжимка, идеи, оценка" },
  { id: "pack", label: "Собираю карточку", hint: "Категории, теги, структура" },
] as const;

function stepIndex(status: string, stage: string | null | undefined, tick: number): number {
  if (status === "queued") return 0;
  if (status === "processing") {
    // Soft progress while backend works — stage hint + time
    if (stage === "preview" || stage === "full") return Math.min(4, 2 + (tick % 3));
    return Math.min(4, 1 + Math.floor(tick / 2) % 4);
  }
  return 0;
}

export function MaterialProcessing({
  title,
  sourceLabel,
  status,
  stage,
  onRetry,
  retryBusy,
}: {
  title?: string | null;
  sourceLabel: string;
  status: string;
  stage?: string | null;
  onRetry?: () => void;
  retryBusy?: boolean;
}) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 1600);
    return () => window.clearInterval(t);
  }, []);

  const active = useMemo(() => stepIndex(status, stage, tick), [status, stage, tick]);
  const failed = status === "failed";

  return (
    <div className="mat-proc" role="status" aria-live="polite">
      <div className="mat-proc-orb" aria-hidden>
        <span className="mat-proc-ring mat-proc-ring--a" />
        <span className="mat-proc-ring mat-proc-ring--b" />
        <span className="mat-proc-ring mat-proc-ring--c" />
        <span className="mat-proc-core">
          <span className="mat-proc-core-glow" />
          <span className="mat-proc-core-mark">{failed ? "!" : "◈"}</span>
        </span>
      </div>

      <p className="mat-proc-kicker">{failed ? "Сбой" : "Обработка"}</p>
      <h1 className="mat-proc-title" style={{ fontFamily: "var(--font-display)" }}>
        {failed ? "Не удалось разобрать" : "Собираю карточку"}
      </h1>
      <p className="mat-proc-sub">
        {failed
          ? "Проверьте источник или ключи API и запустите снова."
          : "Пока идёт разбор, разделы скрыты — появятся сразу после готовности."}
      </p>

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
          {STEPS.map((s, i) => {
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
