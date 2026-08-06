"use client";

import { useEffect, useMemo, useState } from "react";
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
}: {
  materialId: string;
  moments: MomentItem[];
}) {
  const [blobUrls, setBlobUrls] = useState<(string | null)[]>([]);
  const [loading, setLoading] = useState(true);
  const [lightbox, setLightbox] = useState<number | null>(null);

  const fingerprint = useMemo(
    () => moments.map((m) => `${m.t_sec}:${m.image_relpath || ""}`).join("|"),
    [moments]
  );

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

  useEffect(() => {
    if (lightbox == null) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setLightbox(null);
      if (e.key === "ArrowRight") {
        setLightbox((i) => {
          if (i == null) return i;
          const next = moments.findIndex((_, j) => j > i && blobUrls[j]);
          return next >= 0 ? next : i;
        });
      }
      if (e.key === "ArrowLeft") {
        setLightbox((i) => {
          if (i == null) return i;
          for (let j = i - 1; j >= 0; j--) {
            if (blobUrls[j]) return j;
          }
          return i;
        });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox, moments, blobUrls]);

  const withImage = blobUrls.filter(Boolean).length;

  return (
    <section className="mt-10">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Ключевые моменты</h2>
        <p className="text-xs text-[var(--muted)]">
          {loading ? "загрузка…" : `${withImage} из ${moments.length} с кадром`}
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {moments.map((mom, i) => {
          const src = blobUrls[i];
          return (
            <figure
              key={`${mom.t_sec}-${i}-${mom.image_relpath || "x"}`}
              className="overflow-hidden border border-[var(--line)] bg-[var(--surface)]"
            >
              {src ? (
                <button
                  type="button"
                  className="group relative block w-full cursor-zoom-in text-left"
                  onClick={() => setLightbox(i)}
                  aria-label={`Открыть кадр ${fmtTime(mom.t_sec)}`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={src}
                    alt={mom.caption || "момент"}
                    className="aspect-video w-full object-cover transition duration-200 group-hover:brightness-110"
                  />
                  <span className="absolute bottom-2 left-2 rounded bg-black/70 px-2 py-0.5 text-[11px] text-white/90">
                    {fmtTime(mom.t_sec)}
                  </span>
                  <span className="absolute inset-0 flex items-center justify-center bg-black/0 text-sm text-white opacity-0 transition group-hover:bg-black/25 group-hover:opacity-100">
                    Открыть
                  </span>
                </button>
              ) : (
                <div className="flex aspect-video items-center justify-center bg-black/20 text-xs text-[var(--muted)]">
                  {loading ? "…" : `без кадра · ${fmtTime(mom.t_sec)}`}
                </div>
              )}
              <figcaption className="space-y-1 p-3 text-sm">
                <p className="font-medium">{mom.caption || "Момент"}</p>
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

      {lightbox != null && blobUrls[lightbox] && (
        <div
          className="fixed inset-0 z-[80] flex flex-col bg-black/92"
          role="dialog"
          aria-modal="true"
          onClick={() => setLightbox(null)}
        >
          <div className="flex items-center justify-between px-4 py-3 text-sm text-white/70">
            <span>
              {fmtTime(moments[lightbox].t_sec)} · {lightbox + 1}/{moments.length}
            </span>
            <button
              type="button"
              className="px-2 py-1 text-white/80 hover:text-white"
              onClick={() => setLightbox(null)}
            >
              Esc ✕
            </button>
          </div>
          <div
            className="relative flex min-h-0 flex-1 items-center justify-center px-4 pb-6"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="absolute left-2 top-1/2 z-10 -translate-y-1/2 px-3 py-6 text-2xl text-white/60 hover:text-white md:left-6"
              onClick={() => {
                for (let j = lightbox - 1; j >= 0; j--) {
                  if (blobUrls[j]) {
                    setLightbox(j);
                    return;
                  }
                }
              }}
              aria-label="Предыдущий"
            >
              ‹
            </button>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={blobUrls[lightbox]!}
              alt={moments[lightbox].caption || "момент"}
              className="max-h-[78dvh] max-w-full object-contain shadow-2xl"
            />
            <button
              type="button"
              className="absolute right-2 top-1/2 z-10 -translate-y-1/2 px-3 py-6 text-2xl text-white/60 hover:text-white md:right-6"
              onClick={() => {
                for (let j = lightbox + 1; j < moments.length; j++) {
                  if (blobUrls[j]) {
                    setLightbox(j);
                    return;
                  }
                }
              }}
              aria-label="Следующий"
            >
              ›
            </button>
          </div>
          <div className="mx-auto max-w-2xl px-4 pb-8 text-center" onClick={(e) => e.stopPropagation()}>
            <p className="text-base text-white">{moments[lightbox].caption || "Момент"}</p>
            {moments[lightbox].why && (
              <p className="mt-2 text-sm text-white/55">{moments[lightbox].why}</p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
