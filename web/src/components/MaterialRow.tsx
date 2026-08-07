import { useRouter } from "next/navigation";
import type { MaterialListItem } from "@/lib/api";

export const STATUS_LABEL: Record<string, string> = {
  queued: "очередь",
  processing: "обработка",
  preview: "preview",
  awaiting_full: "ждёт full",
  done: "готово",
  failed: "ошибка",
};

export const SOURCE_LABEL: Record<string, string> = {
  youtube: "YouTube",
  instagram: "Instagram",
  video: "Видео",
  article: "Статья",
  note: "Заметка",
  pdf: "PDF",
  voice: "Голос",
  file: "Файл",
};

function statusDot(status: string): string {
  if (status === "done") return "bg-[var(--accent)]";
  if (status === "failed") return "bg-[var(--warn)]";
  if (status === "processing" || status === "queued") return "bg-[var(--muted)] animate-pulse";
  return "bg-[color-mix(in_srgb,var(--accent)_50%,var(--muted))]";
}

export function StatusPill({ status }: { status: string }) {
  const tone =
    status === "done"
      ? "text-[var(--accent)]"
      : status === "failed"
        ? "text-[var(--warn)]"
        : "text-[var(--muted)]";
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] uppercase tracking-[0.14em] ${tone}`}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDot(status)}`} aria-hidden />
      {STATUS_LABEL[status] || status}
    </span>
  );
}

export function formatShortDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export function cleanMetaPart(value: string | null | undefined): string | null {
  if (!value) return null;
  const n = value.trim().toLowerCase();
  if (!n || n === "none" || n === "n/a" || n === "null") return null;
  return value.trim();
}

/** Compact row for archive split-view list */
export function MaterialRow({
  item,
  index,
  active,
  selectable,
  selected,
  onSelect,
  onToggle,
  forceChecks,
}: {
  item: MaterialListItem;
  index: number;
  active?: boolean;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (id: string) => void;
  onToggle?: (id: string) => void;
  forceChecks?: boolean;
}) {
  const router = useRouter();
  const score = item.usefulness_score;
  const dateLabel = formatShortDate(item.created_at);
  const source = SOURCE_LABEL[item.source_type] || item.source_type;
  const category = cleanMetaPart(item.category);
  const meta = [source, category, dateLabel].filter(Boolean) as string[];

  return (
    <div
      role="option"
      aria-selected={active}
      className={`lib-item group relative flex items-start gap-2.5 px-3 py-3 transition ${
        active ? "lib-item-active" : ""
      } ${selected ? "lib-item-checked" : ""}`}
      onClick={() => onSelect?.(item.id)}
      onDoubleClick={(e) => {
        e.preventDefault();
        router.push(`/m/${item.id}`);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect?.(item.id);
        }
      }}
      tabIndex={0}
      style={{ animationDelay: `${Math.min(index, 14) * 22}ms` }}
    >
      {selectable && (
        <label
          className="lib-item-check"
          data-force={forceChecks || selected ? "true" : "false"}
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            checked={Boolean(selected)}
            onChange={() => onToggle?.(item.id)}
            className="peer sr-only"
            aria-label={`Выбрать ${item.title || item.id}`}
          />
          <span className="lib-check peer-focus-visible:ring-2 peer-focus-visible:ring-[var(--accent)]" data-checked={selected || undefined} aria-hidden />
        </label>
      )}

      <span
        className={`w-6 shrink-0 select-none pt-0.5 text-[0.8rem] tabular-nums leading-none text-[color-mix(in_srgb,var(--muted)_55%,transparent)] ${
          selectable ? "hidden sm:inline-block" : ""
        }`}
        style={{ fontFamily: "var(--font-display)" }}
        aria-hidden
      >
        {String(index + 1).padStart(2, "0")}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3">
          <h2
            className={`line-clamp-2 text-[0.98rem] leading-snug tracking-[-0.015em] ${
              active ? "text-[var(--ink)]" : "text-[color-mix(in_srgb,var(--ink)_92%,transparent)]"
            }`}
            style={{ fontFamily: "var(--font-display)" }}
          >
            {item.title || "Без названия"}
          </h2>
          <span
            className={`shrink-0 tabular-nums text-sm leading-none ${
              score != null ? "text-[var(--accent)]" : "text-[var(--muted)]"
            }`}
            style={{ fontFamily: "var(--font-display)" }}
            aria-label={score != null ? `Оценка ${score} из 10` : "Оценка отсутствует"}
          >
            {score != null ? (
              <>
                {score}
                <span className="mx-2.5 text-[color-mix(in_srgb,var(--accent)_45%,var(--muted))]">|</span>
                <span className="text-[color-mix(in_srgb,var(--accent)_55%,var(--muted))]">10</span>
              </>
            ) : (
              "—"
            )}
          </span>
        </div>
        <p className="mt-1 line-clamp-1 text-[0.8rem] text-[var(--muted)]">
          {item.summary || item.main_idea || "Обрабатывается…"}
        </p>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-[0.68rem] text-[color-mix(in_srgb,var(--muted)_88%,transparent)]">
          <span
            className={`mr-0.5 inline-block h-1.5 w-1.5 rounded-full ${statusDot(item.processing_status)}`}
            aria-hidden
          />
          {meta.map((part, i) => (
            <span key={`${part}-${i}`} className="inline-flex items-center gap-1.5">
              {i > 0 && <span className="opacity-40">·</span>}
              {part}
            </span>
          ))}
        </p>
      </div>

      <span className="lib-item-chevron lg:hidden" aria-hidden>
        ›
      </span>
    </div>
  );
}
