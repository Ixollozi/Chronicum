"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  SOURCE_LABEL,
  StatusPill,
  cleanMetaPart,
  formatShortDate,
} from "@/components/MaterialRow";
import { client, type MaterialDetail, type MaterialListItem } from "@/lib/api";

export function ArchivePreview({
  item,
  onDelete,
  onRetry,
  busy,
  retrying,
}: {
  item: MaterialListItem | null;
  onDelete?: (id: string) => void;
  onRetry?: (id: string) => void;
  busy?: boolean;
  retrying?: boolean;
}) {
  const [detail, setDetail] = useState<MaterialDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const itemId = item?.id;
  const itemStatus = item?.processing_status;
  const itemStage = item?.processing_stage;
  const itemTitle = item?.title;
  const itemSummary = item?.summary;

  useEffect(() => {
    if (!itemId) {
      setDetail(null);
      setErr(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setDetail((prev) => (prev && prev.id === itemId ? prev : null));
    setLoading(true);
    setErr(null);
    client
      .getMaterial(itemId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : "Не удалось загрузить");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [itemId, itemStatus, itemStage, itemTitle, itemSummary]);

  if (!item) {
    return (
      <div className="lib-preview flex h-full min-h-[22rem] flex-col items-center justify-center px-8 text-center">
        <p
          className="text-[clamp(1.6rem,2.4vw,2rem)] tracking-[-0.03em] text-[color-mix(in_srgb,var(--muted)_70%,var(--ink))]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          Выберите материал
        </p>
        <p className="mt-3 max-w-xs text-sm leading-relaxed text-[var(--muted)]">
          Список слева — навигация. Справа появится краткий разбор без лишнего.
        </p>
      </div>
    );
  }

  const currentStatus = detail?.processing_status ?? item.processing_status;
  const source = SOURCE_LABEL[item.source_type] || item.source_type;
  const category = cleanMetaPart(item.category);
  const dateLabel = formatShortDate(item.created_at);
  const digest = detail?.digest || detail?.summary || item.summary || item.main_idea;
  const score = detail?.usefulness_score ?? item.usefulness_score;
  const keyIdeas = detail?.key_ideas?.slice(0, 4) || [];
  const takeaways = detail?.practical_takeaways?.slice(0, 3) || [];

  return (
    <div className="lib-preview flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--line)] px-5 py-3.5">
        <StatusPill status={currentStatus} />
        {(detail?.trust_level || item.trust_level) && (
          <span className="archive-chip-meta" title={`confidence ${detail?.analysis_confidence ?? item.analysis_confidence ?? "—"}`}>
            {detail?.trust_level || item.trust_level}
          </span>
        )}
        <span className="archive-chip-meta">{source}</span>
        {category && <span className="archive-chip-meta">{category}</span>}
        {dateLabel && <span className="text-[11px] text-[var(--muted)]">{dateLabel}</span>}
        <div className="ml-auto flex items-center gap-2">
          <div
            className="flex h-9 min-w-[2.6rem] items-center justify-center rounded-full bg-[var(--accent-soft)] px-2.5 tabular-nums shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--accent)_22%,transparent)]"
            style={{ fontFamily: "var(--font-display)" }}
          >
            {score != null ? (
              <span>
                <span className="text-lg text-[var(--accent)]">{score}</span>
                <span className="text-[10px] text-[var(--muted)]">/10</span>
              </span>
            ) : (
              <span className="text-[var(--muted)]">—</span>
            )}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
        <h2
          className="text-[clamp(1.55rem,2.2vw,2.15rem)] leading-[1.12] tracking-[-0.03em] animate-fade"
          key={item.id + "-title"}
          style={{ fontFamily: "var(--font-display)" }}
        >
          {item.title || "Без названия"}
        </h2>

        {loading && !detail ? (
          <div className="mt-6 space-y-3">
            <div className="archive-skeleton h-4 w-full" />
            <div className="archive-skeleton h-4 w-5/6" />
            <div className="archive-skeleton h-4 w-4/6" />
            <div className="archive-skeleton mt-6 h-20 w-full" />
          </div>
        ) : err ? (
          <p className="mt-5 text-sm text-[var(--warn)]">{err}</p>
        ) : (
          <>
            {digest ? (
              <p className="mt-4 text-[0.98rem] leading-relaxed text-[color-mix(in_srgb,var(--ink)_88%,var(--muted))]">
                {digest}
              </p>
            ) : currentStatus === "failed" ? (
              <p className="mt-4 rounded-xl border border-[color-mix(in_srgb,var(--warn)_35%,var(--line))] bg-[color-mix(in_srgb,var(--warn)_8%,transparent)] p-3.5 text-sm leading-relaxed text-[var(--warn)]">
                {detail?.error_message || "Не удалось разобрать материал. Откройте карточку и нажмите «Повторить»."}
              </p>
            ) : currentStatus === "queued" || currentStatus === "processing" ? (
              <p className="mt-4 text-sm leading-relaxed text-[var(--muted)]">
                {currentStatus === "queued" ? "Материал в очереди на обработку…" : "Материал сейчас обрабатывается…"}
              </p>
            ) : null}

            {detail?.usefulness_reason && (
              <p className="mt-5 border-l-2 border-[var(--accent)] pl-3.5 text-sm leading-relaxed text-[var(--muted)]">
                {detail.usefulness_reason}
              </p>
            )}

            {keyIdeas.length > 0 && (
              <section className="mt-7">
                <p className="card-label mb-2.5">Идеи</p>
                <ul className="space-y-2">
                  {keyIdeas.map((idea, i) => (
                    <li key={idea} className="flex gap-2.5 text-sm leading-relaxed text-[var(--muted)]">
                      <span
                        className="shrink-0 tabular-nums text-[var(--accent)]"
                        style={{ fontFamily: "var(--font-display)" }}
                      >
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span>{idea}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {takeaways.length > 0 && (
              <section className="mt-6">
                <p className="card-label mb-2.5">Выводы</p>
                <ul className="space-y-2">
                  {takeaways.map((t) => (
                    <li key={t} className="flex gap-2 text-sm text-[var(--muted)]">
                      <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-[var(--accent)]" />
                      <span>{t}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {!!item.tags?.length && (
              <div className="mt-7 flex flex-wrap gap-1.5">
                {item.tags.slice(0, 8).map((t) => (
                  <span key={t} className="archive-chip-meta">
                    #{t}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-[var(--line)] px-5 py-3.5">
        <Link href={`/m/${item.id}`} className="archive-cta">
          Открыть карточку
        </Link>
        {currentStatus === "failed" && onRetry && (
          <button
            type="button"
            disabled={retrying || busy}
            className="card-btn card-btn-accent"
            onClick={() => onRetry(item.id)}
          >
            {retrying ? "Повторяю…" : "Повторить"}
          </button>
        )}
        {item.source_url && (
          <a
            href={item.source_url}
            target="_blank"
            rel="noreferrer"
            className="card-btn"
          >
            Источник
          </a>
        )}
        {onDelete && (
          <button
            type="button"
            disabled={busy}
            className="card-btn card-btn-danger ml-auto"
            onClick={() => onDelete(item.id)}
          >
            Удалить
          </button>
        )}
      </div>
    </div>
  );
}
