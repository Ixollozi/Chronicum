"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MomentsGallery } from "@/components/MomentsGallery";
import { StatusPill } from "@/components/MaterialRow";
import { client, type MaterialDetail } from "@/lib/api";

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

  async function load() {
    try {
      const data = await client.getMaterial(id);
      setM(data);
      if (data.usefulness_score) setScore(data.usefulness_score);
      setReasonEdit(data.usefulness_reason || "");
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

  if (error) return <p className="text-[var(--warn)]">{error}</p>;
  if (!m) return <p className="text-[var(--muted)] animate-fade">Открываю карточку…</p>;

  const isVideo = ["youtube", "instagram", "video"].includes(m.source_type);
  const digest = m.digest || m.summary;
  const evalObj = m.objective_evaluation;

  return (
    <article className="grid gap-10 lg:grid-cols-[1.4fr_0.8fr]">
      <div className="animate-rise">
        <div className="mb-4 flex flex-wrap items-center gap-3 text-sm text-[var(--muted)]">
          <StatusPill status={m.processing_status} />
          <span>{m.source_type}</span>
          {m.processing_stage && <span>stage: {m.processing_stage}</span>}
        </div>
        <h1
          className="text-[clamp(2rem,4vw,3rem)] leading-[1.05] tracking-[-0.03em]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {m.title || "Без названия"}
        </h1>
        {m.source_url && (
          <a
            href={m.source_url}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-block text-sm text-[var(--accent)] hover:underline"
          >
            Открыть источник →
          </a>
        )}

        {digest && (
          <section className="mt-8 border border-[var(--line)] bg-[var(--surface)] p-5">
            <h2 className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Выжимка</h2>
            <p className="mt-3 whitespace-pre-wrap text-lg leading-relaxed">{digest}</p>
          </section>
        )}

        {m.main_idea && (
          <p className="mt-6 max-w-2xl border-l-2 border-[var(--accent)] pl-4 text-[var(--muted)]">
            {m.main_idea}
          </p>
        )}

        {m.summary && m.digest && m.summary !== m.digest && (
          <p className="mt-6 max-w-2xl leading-relaxed text-[var(--muted)]">{m.summary}</p>
        )}

        <div className="mt-8 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={!!busy}
            className="border border-[var(--line)] px-3 py-2 text-sm hover:border-[var(--accent)] disabled:opacity-40"
            onClick={() => run("digest", () => client.digest(m.id))}
          >
            {busy === "digest" ? "…" : "Обновить выжимку"}
          </button>
          <button
            type="button"
            disabled={!!busy}
            className="border border-[var(--accent)] px-3 py-2 text-sm text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--btn-fg)] disabled:opacity-40"
            onClick={() => run("explain", () => client.explain(m.id))}
          >
            {busy === "explain" ? "…" : "Подробнее"}
          </button>
          <button
            type="button"
            disabled={!!busy}
            className="border border-[var(--line)] px-3 py-2 text-sm hover:border-[var(--accent)] disabled:opacity-40"
            onClick={() => run("evaluate", () => client.evaluate(m.id))}
          >
            {busy === "evaluate" ? "…" : "Объективная оценка"}
          </button>
          <button
            type="button"
            disabled={!!busy}
            className="border border-[var(--accent)] bg-[var(--accent)] px-3 py-2 text-sm text-[var(--btn-fg)] disabled:opacity-40"
            onClick={() => run("opinion", () => client.opinion(m.id))}
          >
            {busy === "opinion" ? "…" : "Спросить мнение у ИИ"}
          </button>
          {isVideo && (
            <button
              type="button"
              disabled={!!busy}
              className="border border-[var(--line)] px-3 py-2 text-sm hover:border-[var(--accent)] disabled:opacity-40"
              onClick={() => run("moments", () => client.moments(m.id))}
            >
              {busy === "moments" ? "…" : "Ключевые кадры"}
            </button>
          )}
          <button
            type="button"
            disabled={!!busy}
            className="border border-[var(--warn)] px-3 py-2 text-sm text-[var(--warn)] hover:bg-[var(--warn)] hover:text-[var(--btn-fg)] disabled:opacity-40"
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
        {actionErr && <p className="mt-3 text-sm text-[var(--warn)]">{actionErr}</p>}

        {m.detailed_explanation && (
          <section className="mt-10">
            <h2 className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Подробное объяснение</h2>
            <div className="whitespace-pre-wrap leading-relaxed">{m.detailed_explanation}</div>
          </section>
        )}

        {evalObj && (
          <section className="mt-10 border border-[var(--line)] p-5">
            <h2 className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Объективная оценка</h2>
            {evalObj.verdict && <p className="text-lg">{evalObj.verdict}</p>}
            {evalObj.best_for && (
              <p className="mt-2 text-sm text-[var(--muted)]">Кому: {evalObj.best_for}</p>
            )}
            {!!evalObj.pros?.length && (
              <div className="mt-4">
                <p className="text-xs text-[var(--muted)]">Плюсы</p>
                <ul className="mt-1 list-disc pl-5">
                  {evalObj.pros.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </div>
            )}
            {!!evalObj.cons?.length && (
              <div className="mt-3">
                <p className="text-xs text-[var(--muted)]">Минусы</p>
                <ul className="mt-1 list-disc pl-5">
                  {evalObj.cons.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </div>
            )}
            {!!evalObj.risks?.length && (
              <div className="mt-3">
                <p className="text-xs text-[var(--muted)]">Риски</p>
                <ul className="mt-1 list-disc pl-5">
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
            <h2 className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Мнение ИИ (интернет)</h2>
            <div className="whitespace-pre-wrap leading-relaxed">{m.ai_opinion}</div>
            {!!m.ai_opinion_sources?.length && (
              <ul className="mt-4 space-y-1 text-sm text-[var(--muted)]">
                {m.ai_opinion_sources.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            )}
          </section>
        )}

        {!!m.key_moments?.length && <MomentsGallery materialId={m.id} moments={m.key_moments} />}
        {m.key_ideas?.length > 0 && (
          <section className="mt-10">
            <h2 className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Ключевые идеи</h2>
            <ul className="space-y-2">
              {m.key_ideas.map((x) => (
                <li key={x} className="border-b border-[var(--line)] py-2">
                  {x}
                </li>
              ))}
            </ul>
          </section>
        )}

        {m.practical_takeaways?.length > 0 && (
          <section className="mt-8">
            <h2 className="mb-3 text-xs uppercase tracking-[0.18em] text-[var(--muted)]">
              Практические выводы
            </h2>
            <ul className="space-y-2">
              {m.practical_takeaways.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </section>
        )}

        {m.raw_text && (
          <details className="mt-10">
            <summary className="cursor-pointer text-sm text-[var(--muted)]">Полный текст / транскрипт</summary>
            <pre className="mt-4 max-h-[420px] overflow-auto whitespace-pre-wrap border border-[var(--line)] bg-[var(--surface)] p-4 text-sm leading-relaxed">
              {m.raw_text}
            </pre>
          </details>
        )}
      </div>

      <aside className="space-y-6 animate-fade lg:sticky lg:top-8 lg:self-start">
        <div className="border border-[var(--line)] bg-[var(--surface)] p-5">
          <p className="text-xs uppercase tracking-[0.18em] text-[var(--muted)]">Полезность</p>
          <p className="mt-2 text-3xl tabular-nums" style={{ fontFamily: "var(--font-display)" }}>
            {m.usefulness_score ?? "—"}
            <span className="text-base text-[var(--muted)]"> / 10</span>
          </p>
          <p className="mt-2 text-sm text-[var(--muted)]">{m.usefulness_reason}</p>
          <div className="mt-4 flex items-center gap-2">
            <input
              type="range"
              min={1}
              max={10}
              value={score}
              onChange={(e) => setScore(Number(e.target.value))}
              className="w-full accent-[var(--accent)]"
            />
            <button
              className="border border-[var(--accent)] bg-[var(--accent)] px-3 py-1 text-sm text-[var(--btn-fg)]"
              onClick={async () => {
                const updated = await client.updateScore(m.id, score, reasonEdit.trim() || undefined);
                setM(updated);
              }}
            >
              OK
            </button>
          </div>
          <textarea
            value={reasonEdit}
            onChange={(e) => setReasonEdit(e.target.value)}
            rows={2}
            placeholder="Почему такая оценка (опционально)"
            className="mt-3 w-full resize-y border border-[var(--line)] bg-transparent p-2 text-sm outline-none focus:border-[var(--accent)]"
          />
        </div>

        <div className="border border-[var(--line)] bg-[var(--surface)] p-5 text-sm">
          <p>
            <span className="text-[var(--muted)]">Категория:</span> {m.category || "—"}
          </p>
          <p className="mt-2">
            <span className="text-[var(--muted)]">Теги:</span>{" "}
            {m.tags.length ? m.tags.join(", ") : "—"}
          </p>
          {(m.tools?.length || m.technologies?.length) > 0 && (
            <p className="mt-2">
              <span className="text-[var(--muted)]">Инструменты / tech:</span>{" "}
              {[...(m.tools || []), ...(m.technologies || [])].join(", ")}
            </p>
          )}
          {m.error_message && <p className="mt-3 text-[var(--warn)]">{m.error_message}</p>}
        </div>

        <div className="flex flex-wrap gap-2">
          {m.processing_status === "awaiting_full" && (
            <p className="mb-2 w-full text-sm text-[var(--accent)]">
              Превью готово, полный разбор не запущен (score ниже порога). Можно запустить вручную.
            </p>
          )}
          <button
            className="border border-[var(--line)] px-3 py-2 text-sm hover:border-[var(--accent)] hover:bg-[var(--accent-soft)] hover:text-[var(--accent)]"
            onClick={async () => {
              await client.retry(m.id);
              await load();
            }}
          >
            Retry
          </button>
          <button
            className={
              m.processing_status === "awaiting_full"
                ? "border border-[var(--accent)] bg-[var(--accent)] px-3 py-2 text-sm text-[var(--btn-fg)]"
                : "border border-[var(--accent)] px-3 py-2 text-sm text-[var(--accent)] hover:bg-[var(--accent)] hover:text-[var(--btn-fg)]"
            }
            onClick={async () => {
              await client.processFull(m.id);
              await load();
            }}
          >
            Полная обработка
          </button>
        </div>

        <p className="text-xs text-[var(--muted)]">id: {m.id}</p>
      </aside>
    </article>
  );
}
