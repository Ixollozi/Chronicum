import type { MaterialListItem } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  queued: "в очереди",
  processing: "обработка",
  preview: "preview",
  awaiting_full: "ждёт full",
  done: "готово",
  failed: "ошибка",
};

export function StatusPill({ status }: { status: string }) {
  const tone =
    status === "done"
      ? "text-[var(--accent)]"
      : status === "failed"
        ? "text-[var(--warn)]"
        : "text-[var(--muted)]";
  return (
    <span className={`text-[11px] uppercase tracking-[0.16em] ${tone}`}>
      {STATUS_LABEL[status] || status}
    </span>
  );
}

export function MaterialRow({
  item,
  index,
  selectable,
  selected,
  onToggle,
}: {
  item: MaterialListItem;
  index: number;
  selectable?: boolean;
  selected?: boolean;
  onToggle?: (id: string) => void;
}) {
  return (
    <div
      className={`group grid items-baseline gap-x-4 gap-y-2 border-b border-[var(--line)] py-5 transition hover:bg-[var(--surface-hover)] animate-rise ${
        selectable
          ? "grid-cols-[auto_auto_1fr_auto] md:grid-cols-[auto_4rem_1fr_7rem_6rem]"
          : "grid-cols-[auto_1fr_auto] md:grid-cols-[4rem_1fr_7rem_6rem]"
      }`}
      style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
    >
      {selectable && (
        <label className="flex items-center self-center">
          <input
            type="checkbox"
            checked={Boolean(selected)}
            onChange={() => onToggle?.(item.id)}
            className="accent-[var(--accent)]"
            aria-label={`Выбрать ${item.title || item.id}`}
          />
        </label>
      )}
      <span className="text-xs tabular-nums text-[var(--muted)]">
        {String(index + 1).padStart(2, "0")}
      </span>
      <div className="min-w-0">
        <a href={`/m/${item.id}`} className="block">
          <h2
            className="truncate text-xl leading-snug tracking-[-0.02em] transition group-hover:text-[var(--accent)] md:text-[1.35rem]"
            style={{ fontFamily: "var(--font-display)" }}
          >
            {item.title || "Без названия"}
          </h2>
        </a>
        <p className="mt-1 line-clamp-2 text-sm text-[var(--muted)]">
          {item.summary || item.main_idea || "Ещё обрабатывается…"}
        </p>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--muted)]">
          <span>{item.source_type}</span>
          {item.category && <span>{item.category}</span>}
          {item.tags.slice(0, 3).map((t) => (
            <span key={t}>#{t}</span>
          ))}
        </div>
      </div>
      <div className="hidden text-right md:block">
        <StatusPill status={item.processing_status} />
      </div>
      <div className="text-right">
        <span className="tabular-nums text-sm">
          {item.usefulness_score != null ? `${item.usefulness_score}/10` : "—"}
        </span>
      </div>
    </div>
  );
}
