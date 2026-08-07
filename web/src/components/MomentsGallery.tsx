"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getToken } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type MomentItem = {
  t_sec: number;
  caption: string;
  why: string;
  when_to_apply: string;
  quote?: string;
  visible_text?: string;
  image_relpath?: string | null;
};

function fmtTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

export function MomentsGallery({
  materialId,
  moments,
  embedded,
}: {
  materialId: string;
  moments: MomentItem[];
  embedded?: boolean;
}) {
  const [blobUrls, setBlobUrls] = useState<(string | null)[]>([]);
  const [loading, setLoading] = useState(true);
  const [index, setIndex] = useState<number | null>(null);
  const [visible, setVisible] = useState(false);
  const [mounted, setMounted] = useState(false);
  const closingRef = useRef(false);
  const touchRef = useRef<{ x: number; y: number } | null>(null);

  const fingerprint = useMemo(
    () => moments.map((m) => `${m.t_sec}:${m.image_relpath || ""}`).join("|"),
    [moments]
  );

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const created: string[] = [];

    async function load() {
      setLoading(true);
      const results = await Promise.all(
        moments.map(async (mom) => {
          const fname = mom.image_relpath?.split("/").pop();
          if (!fname) return null;
          try {
            const res = await fetch(
              `${API_URL}/api/materials/${materialId}/moments/${encodeURIComponent(fname)}`,
              { headers: { Authorization: `Bearer ${getToken()}` } }
            );
            if (!res.ok) return null;
            const blob = await res.blob();
            if (!blob.type.startsWith("image/") && blob.size < 500) return null;
            const url = URL.createObjectURL(blob);
            created.push(url);
            return url;
          } catch {
            return null;
          }
        })
      );
      if (!cancelled) {
        setBlobUrls(results);
        setLoading(false);
      } else {
        created.forEach((u) => URL.revokeObjectURL(u));
      }
    }

    load();
    return () => {
      cancelled = true;
      created.forEach((u) => URL.revokeObjectURL(u));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [materialId, fingerprint]);

  function openAt(i: number) {
    if (!blobUrls[i]) return;
    closingRef.current = false;
    setIndex(i);
    requestAnimationFrame(() => setVisible(true));
  }

  function closeLightbox() {
    if (closingRef.current || index == null) return;
    closingRef.current = true;
    setVisible(false);
    window.setTimeout(() => {
      if (!closingRef.current) return;
      setIndex(null);
      closingRef.current = false;
    }, 280);
  }

  function goPrev() {
    setIndex((i) => {
      if (i == null) return i;
      for (let j = i - 1; j >= 0; j--) {
        if (blobUrls[j]) return j;
      }
      return i;
    });
  }

  function goNext() {
    setIndex((i) => {
      if (i == null) return i;
      const next = moments.findIndex((_, j) => j > i && blobUrls[j]);
      return next >= 0 ? next : i;
    });
  }

  useEffect(() => {
    if (index == null) return;

    const scrollY = window.scrollY;
    const { style } = document.body;
    const prev = {
      overflow: style.overflow,
      position: style.position,
      top: style.top,
      width: style.width,
    };
    style.overflow = "hidden";
    style.position = "fixed";
    style.top = `-${scrollY}px`;
    style.width = "100%";

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeLightbox();
      if (e.key === "ArrowRight") goNext();
      if (e.key === "ArrowLeft") goPrev();
    }
    window.addEventListener("keydown", onKey);

    return () => {
      window.removeEventListener("keydown", onKey);
      style.overflow = prev.overflow;
      style.position = prev.position;
      style.top = prev.top;
      style.width = prev.width;
      window.scrollTo(0, scrollY);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index != null, blobUrls]);

  const withImage = blobUrls.filter(Boolean).length;
  const active = index != null ? moments[index] : null;
  const activeSrc = index != null ? blobUrls[index] : null;
  const canPrev = index != null && blobUrls.slice(0, index).some(Boolean);
  const canNext = index != null && blobUrls.slice(index + 1).some(Boolean);

  const lightbox =
    mounted &&
    index != null &&
    active &&
    activeSrc &&
    createPortal(
      <div
        className="moments-lightbox"
        data-open={visible ? "true" : "false"}
        role="dialog"
        aria-modal="true"
        aria-label="Просмотр кадра"
        onTransitionEnd={(e) => {
          if (e.target !== e.currentTarget) return;
          if (!visible && closingRef.current) {
            setIndex(null);
            closingRef.current = false;
          }
        }}
        onClick={closeLightbox}
        onTouchStart={(e) => {
          const t = e.changedTouches[0];
          touchRef.current = t ? { x: t.clientX, y: t.clientY } : null;
        }}
        onTouchEnd={(e) => {
          if (!touchRef.current) return;
          const t = e.changedTouches[0];
          if (!t) return;
          const dx = t.clientX - touchRef.current.x;
          const dy = t.clientY - touchRef.current.y;
          touchRef.current = null;
          if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
          if (dx < 0) goNext();
          else goPrev();
        }}
      >
        <header className="moments-lightbox-bar" onClick={(e) => e.stopPropagation()}>
          <p className="moments-lightbox-index tabular-nums">
            {fmtTime(active.t_sec)}
            <span className="opacity-40"> · </span>
            {index + 1}/{moments.length}
          </p>
          <button
            type="button"
            className="moments-lightbox-close"
            aria-label="Закрыть"
            onClick={closeLightbox}
          >
            ✕
          </button>
        </header>

        <div className="moments-lightbox-stage" onClick={(e) => e.stopPropagation()}>
          <button
            type="button"
            className="moments-lightbox-nav prev"
            disabled={!canPrev}
            onClick={goPrev}
            aria-label="Предыдущий"
          >
            ‹
          </button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            key={activeSrc}
            src={activeSrc}
            alt={active.caption || "момент"}
            className="moments-lightbox-img"
            draggable={false}
          />
          <button
            type="button"
            className="moments-lightbox-nav next"
            disabled={!canNext}
            onClick={goNext}
            aria-label="Следующий"
          >
            ›
          </button>
        </div>

        <footer className="moments-lightbox-cap" onClick={(e) => e.stopPropagation()}>
          <p className="moments-lightbox-title">{active.caption || "Момент"}</p>
          {active.why && <p className="moments-lightbox-why">{active.why}</p>}
        </footer>
      </div>,
      document.body
    );

  return (
    <div className={`moments-gallery ${embedded ? "" : "mt-10"}`.trim()}>
      {!embedded ? (
        <div className="mb-4 flex items-baseline justify-between gap-3">
          <h2 className="card-label">Ключевые моменты</h2>
          <p className="text-xs tabular-nums text-[var(--muted)]">
            {loading ? "загрузка…" : `${withImage} / ${moments.length} кадров`}
          </p>
        </div>
      ) : (
        <p className="mb-3 text-xs tabular-nums text-[var(--muted)]">
          {loading ? "загрузка…" : `${withImage} / ${moments.length} кадров`}
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {moments.map((mom, i) => {
          const src = blobUrls[i];
          return (
            <figure
              key={`${mom.t_sec}-${i}-${mom.image_relpath || "x"}`}
              className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--line)] bg-[var(--surface)] transition hover:border-[color-mix(in_srgb,var(--accent)_35%,var(--line))]"
            >
              {src ? (
                <button
                  type="button"
                  className="group relative block w-full cursor-zoom-in text-left"
                  onClick={() => openAt(i)}
                  aria-label={`Открыть кадр ${fmtTime(mom.t_sec)}`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={src}
                    alt={mom.caption || "момент"}
                    className="aspect-video w-full object-cover transition duration-200 group-hover:brightness-110"
                  />
                  <span className="absolute bottom-2 left-2 rounded-md bg-black/70 px-2 py-0.5 text-[11px] tabular-nums text-white/90">
                    {fmtTime(mom.t_sec)}
                  </span>
                  <span className="pointer-events-none absolute right-2 top-2 rounded-full bg-black/55 px-2.5 py-1 text-[11px] text-white/90 opacity-90 transition group-hover:bg-black/70 md:opacity-0 md:group-hover:opacity-100">
                    Открыть
                  </span>
                </button>
              ) : (
                <div className="flex aspect-video items-center justify-center bg-black/20 text-xs text-[var(--muted)]">
                  {loading ? (
                    <span className="archive-skeleton h-3 w-16" />
                  ) : (
                    `без кадра · ${fmtTime(mom.t_sec)}`
                  )}
                </div>
              )}
              <figcaption className="space-y-1.5 p-3.5 text-sm">
                <p className="font-medium leading-snug">{mom.caption || "Момент"}</p>
                {mom.visible_text && (
                  <p className="text-xs text-[var(--muted)]">На экране: {mom.visible_text}</p>
                )}
                {mom.why && <p className="text-[var(--muted)]">Зачем: {mom.why}</p>}
                {mom.when_to_apply && (
                  <p className="text-[var(--muted)]">Когда: {mom.when_to_apply}</p>
                )}
              </figcaption>
            </figure>
          );
        })}
      </div>

      {lightbox}
    </div>
  );
}
