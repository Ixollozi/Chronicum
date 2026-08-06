"use client";

import { useCallback, useEffect, useState } from "react";
import { client, type ApiKeyItem, type AppSettings, type PersonalProfile } from "@/lib/api";

const emptyProfile = (): PersonalProfile => ({
  role: "",
  goals: [],
  focuses: [],
  anti_focuses: [],
  horizon: "90d",
  current_priorities: "",
});

function linesToList(text: string): string[] {
  return text
    .replace(/\r/g, "\n")
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function listToLines(items: string[] | undefined): string {
  return (items || []).join("\n");
}

export default function SettingsPage() {
  const [tab, setTab] = useState<"modes" | "context" | "keys">("modes");
  const [provider, setProvider] = useState<"groq" | "google">("groq");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [label, setLabel] = useState("");
  const [keysBlob, setKeysBlob] = useState("");
  const [showKeys, setShowKeys] = useState(false);
  const [checkMsg, setCheckMsg] = useState<string | null>(null);
  const [profileSaved, setProfileSaved] = useState<string | null>(null);

  // Draft editors for profile lists
  const [role, setRole] = useState("");
  const [goalsText, setGoalsText] = useState("");
  const [focusesText, setFocusesText] = useState("");
  const [antiText, setAntiText] = useState("");
  const [horizon, setHorizon] = useState<string>("90d");
  const [priorities, setPriorities] = useState("");

  const syncProfileDraft = useCallback((p: PersonalProfile | undefined) => {
    const prof = p || emptyProfile();
    setRole(prof.role || "");
    setGoalsText(listToLines(prof.goals));
    setFocusesText(listToLines(prof.focuses));
    setAntiText(listToLines(prof.anti_focuses));
    setHorizon(prof.horizon || "90d");
    setPriorities(prof.current_priorities || "");
  }, []);

  const loadSettings = useCallback(async () => {
    const s = await client.getSettings();
    setSettings(s);
    syncProfileDraft(s.personal_profile);
  }, [syncProfileDraft]);

  const loadKeys = useCallback(async () => {
    setKeys(await client.listKeys(provider));
  }, [provider]);

  useEffect(() => {
    loadSettings().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [loadSettings]);

  useEffect(() => {
    if (tab === "keys") {
      loadKeys().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
    }
  }, [tab, loadKeys]);

  const crmCount = keys.filter((k) => k.source === "crm").length;
  const poolCount = keys.length;

  const profileEmpty =
    !role.trim() &&
    !goalsText.trim() &&
    !focusesText.trim() &&
    !antiText.trim() &&
    !priorities.trim();

  async function saveProfile() {
    setBusy(true);
    setError(null);
    setProfileSaved(null);
    try {
      const next = await client.patchSettings({
        personal_profile: {
          role: role.trim(),
          goals: linesToList(goalsText),
          focuses: linesToList(focusesText),
          anti_focuses: linesToList(antiText),
          horizon,
          current_priorities: priorities.trim(),
        },
      });
      setSettings(next);
      syncProfileDraft(next.personal_profile);
      setProfileSaved("Сохранено — учтётся в новых разборах");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto max-w-2xl animate-rise">
      <h1
        className="text-[clamp(2rem,5vw,2.8rem)] leading-none tracking-[-0.03em]"
        style={{ fontFamily: "var(--font-display)" }}
      >
        {tab === "keys" ? "AI · пулы ключей" : tab === "context" ? "Личный контекст" : "Настройки"}
      </h1>
      <p className="mt-3 text-[var(--muted)]">
        {tab === "context"
          ? "Оценка полезности калибруется под твои цели и фокусы (±5 к базе)."
          : "Сначала используется Groq, при исчерпании — Gemini. Ключи из UI и опционально из .env."}
      </p>

      <div className="mt-8 flex flex-wrap gap-2 border-b border-[var(--line)]">
        <button
          type="button"
          onClick={() => setTab("modes")}
          className={`px-4 py-2 text-sm ${tab === "modes" ? "border-b-2 border-[var(--accent)] text-[var(--accent)]" : "text-[var(--muted)]"}`}
        >
          Режимы
        </button>
        <button
          type="button"
          onClick={() => setTab("context")}
          className={`px-4 py-2 text-sm ${tab === "context" ? "border-b-2 border-[var(--accent)] text-[var(--accent)]" : "text-[var(--muted)]"}`}
        >
          Контекст
        </button>
        <button
          type="button"
          onClick={() => setTab("keys")}
          className={`px-4 py-2 text-sm ${tab === "keys" ? "border-b-2 border-[var(--accent)] text-[var(--accent)]" : "text-[var(--muted)]"}`}
        >
          Пулы ключей
        </button>
      </div>

      {error && <p className="mt-4 text-sm text-[var(--warn)]">{error}</p>}

      {tab === "modes" && settings && (
        <div className="mt-8 space-y-6">
          <label className="block">
            <span className="text-sm text-[var(--muted)]">Глубина анализа по умолчанию</span>
            <select
              value={settings.default_depth}
              onChange={async (e) => {
                const next = await client.patchSettings({ default_depth: e.target.value });
                setSettings(next);
              }}
              className="mt-2 w-full border-b border-[var(--line)] bg-transparent py-2 outline-none"
            >
              <option value="compact">compact — только выжимка</option>
              <option value="standard">standard — карточка + выжимка</option>
              <option value="detailed">detailed — сразу подробности и оценка</option>
            </select>
          </label>
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={settings.auto_moments}
              onChange={async (e) => {
                const next = await client.patchSettings({ auto_moments: e.target.checked });
                setSettings(next);
              }}
            />
            Автоскриншоты ключевых моментов для видео
          </label>
          <p className="text-sm text-[var(--muted)]">
            Порядок провайдеров: {(settings.provider_order || []).join(" → ")}
          </p>
          <p className="text-sm text-[var(--muted)]">
            Личный профиль для оценки — во вкладке{" "}
            <button type="button" className="text-[var(--accent)] underline" onClick={() => setTab("context")}>
              Контекст
            </button>
            .
          </p>
        </div>
      )}

      {tab === "context" && (
        <div className="mt-8 space-y-5">
          {profileEmpty && (
            <p className="border border-[var(--line)] bg-[var(--surface)] px-4 py-3 text-sm text-[var(--muted)]">
              Профиль пуст — оценка сейчас нейтральная (как для «среднего» читателя). Заполни, чтобы score
              учитывал твои цели.
            </p>
          )}

          <label className="block">
            <span className="text-sm text-[var(--muted)]">Роль</span>
            <input
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="Владелец AI-агентства / продакт / …"
              className="mt-2 w-full border-b border-[var(--line)] bg-transparent py-2 outline-none"
            />
          </label>

          <label className="block">
            <span className="text-sm text-[var(--muted)]">Цели (по строке или через запятую)</span>
            <textarea
              value={goalsText}
              onChange={(e) => setGoalsText(e.target.value)}
              rows={3}
              placeholder={"Масштабировать delivery\nЗакрывать сделки\nСистемно копить знания"}
              className="mt-2 w-full resize-y border border-[var(--line)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]"
            />
          </label>

          <label className="block">
            <span className="text-sm text-[var(--muted)]">Фокусы — повышают оценку</span>
            <textarea
              value={focusesText}
              onChange={(e) => setFocusesText(e.target.value)}
              rows={3}
              placeholder={"AI / LLM\nCapCut / монтаж\nДизайн\nПродажи агентства"}
              className="mt-2 w-full resize-y border border-[var(--line)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]"
            />
          </label>

          <label className="block">
            <span className="text-sm text-[var(--muted)]">Анти-фокусы — режут оценку</span>
            <textarea
              value={antiText}
              onChange={(e) => setAntiText(e.target.value)}
              rows={2}
              placeholder={"Крипта\nГеймдев\n…"}
              className="mt-2 w-full resize-y border border-[var(--line)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]"
            />
          </label>

          <label className="block">
            <span className="text-sm text-[var(--muted)]">Горизонт применения</span>
            <select
              value={horizon}
              onChange={(e) => setHorizon(e.target.value)}
              className="mt-2 w-full border-b border-[var(--line)] bg-transparent py-2 outline-none"
            >
              <option value="now">сейчас / эта неделя</option>
              <option value="90d">~90 дней</option>
              <option value="long">долгосрочно</option>
            </select>
          </label>

          <label className="block">
            <span className="text-sm text-[var(--muted)]">Текущие приоритеты (1–3)</span>
            <textarea
              value={priorities}
              onChange={(e) => setPriorities(e.target.value)}
              rows={3}
              placeholder="Сейчас важнее всего: упаковать офферы, ускорить монтаж Reels, наладить базу знаний…"
              className="mt-2 w-full resize-y border border-[var(--line)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]"
            />
          </label>

          <div className="flex flex-wrap items-center gap-3 pt-2">
            <button
              type="button"
              disabled={busy}
              onClick={saveProfile}
              className="border border-[var(--accent)] bg-[var(--accent)] px-4 py-2 text-sm text-[var(--btn-fg)] disabled:opacity-40"
            >
              {busy ? "…" : "Сохранить профиль"}
            </button>
            {profileSaved && <span className="text-sm text-[var(--accent)]">{profileSaved}</span>}
          </div>
          <p className="text-xs text-[var(--muted)]">
            Уже сохранённые материалы не пересчитываются сами — только новые разборы / Retry.
          </p>
        </div>
      )}

      {tab === "keys" && (
        <div className="mt-6">
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setProvider("groq")}
              className={`rounded-xl border px-4 py-3 text-left ${
                provider === "groq"
                  ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                  : "border-[var(--line)]"
              }`}
            >
              <p className="font-medium">Groq</p>
              <p className="text-xs text-[var(--muted)]">Основной · быстро</p>
            </button>
            <button
              type="button"
              onClick={() => setProvider("google")}
              className={`rounded-xl border px-4 py-3 text-left ${
                provider === "google"
                  ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                  : "border-[var(--line)]"
              }`}
            >
              <p className="font-medium">Gemini</p>
              <p className="text-xs text-[var(--muted)]">Fallback Google</p>
            </button>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy}
              className="border border-[var(--accent)] bg-[var(--accent)] px-3 py-2 text-sm text-[var(--btn-fg)] disabled:opacity-50"
              onClick={async () => {
                setBusy(true);
                setCheckMsg(null);
                try {
                  const r = await client.checkKeys(provider);
                  setCheckMsg(`OK: ${r.ok}, fail: ${r.failed}`);
                  await loadKeys();
                } catch (e) {
                  setError(e instanceof Error ? e.message : "Ошибка проверки");
                } finally {
                  setBusy(false);
                }
              }}
            >
              Проверить {provider === "groq" ? "Groq" : "Gemini"}
            </button>
            <button
              type="button"
              className="border border-[var(--line)] px-3 py-2 text-sm hover:border-[var(--accent)]"
              onClick={() => loadKeys()}
            >
              Обновить
            </button>
            <span className="text-xs text-[var(--muted)]">
              В пуле: {poolCount} · CRM: {crmCount}
            </span>
            {checkMsg && <span className="text-xs text-[var(--accent)]">{checkMsg}</span>}
          </div>

          <ul className="mt-6 space-y-2">
            {keys.map((k) => (
              <li
                key={String(k.id)}
                className="flex flex-wrap items-center justify-between gap-3 border border-[var(--line)] bg-[var(--surface)] px-4 py-3"
              >
                <div>
                  <p className="text-sm font-medium">{k.label || "без подписи"}</p>
                  <p className="text-xs text-[var(--muted)]">
                    ••••{k.hint} · {k.source}
                    {k.last_error ? ` · ${k.last_error.slice(0, 40)}` : ""}
                  </p>
                </div>
                <div className="flex gap-2">
                  {k.source === "crm" && typeof k.id === "number" && (
                    <>
                      <button
                        type="button"
                        className="border border-[var(--line)] px-2 py-1 text-xs"
                        onClick={async () => {
                          await client.patchKey(k.id as number, { enabled: !k.enabled });
                          await loadKeys();
                        }}
                      >
                        {k.enabled ? "Выкл" : "Вкл"}
                      </button>
                      <button
                        type="button"
                        className="border border-[var(--line)] px-2 py-1 text-xs text-[var(--warn)]"
                        onClick={async () => {
                          await client.deleteKey(k.id as number);
                          await loadKeys();
                        }}
                      >
                        Удалить
                      </button>
                    </>
                  )}
                  {k.source === "env" && (
                    <span className="text-xs text-[var(--muted)]">из .env</span>
                  )}
                </div>
              </li>
            ))}
            {keys.length === 0 && (
              <p className="text-sm text-[var(--muted)]">Пул пуст — добавь ключи ниже или в .env</p>
            )}
          </ul>

          <div className="mt-8 border border-[var(--line)] bg-[var(--surface)] p-4">
            <h2 className="text-sm font-medium">
              Добавить ключи {provider === "groq" ? "Groq" : "Gemini"}
            </h2>
            <p className="mt-1 text-xs text-[var(--muted)]">
              Один или несколько через Enter / запятую. Полный ключ после сохранения не показывается.
            </p>
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Подпись (необязательно)"
              className="mt-4 w-full border-b border-[var(--line)] bg-transparent py-2 text-sm outline-none"
            />
            <div className="relative mt-3">
              <textarea
                value={keysBlob}
                onChange={(e) => setKeysBlob(e.target.value)}
                rows={3}
                placeholder={provider === "groq" ? "gsk_..." : "AIza..."}
                className="w-full resize-y border border-[var(--line)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]"
                style={showKeys ? undefined : ({ WebkitTextSecurity: "disc" } as React.CSSProperties)}
              />
              <button
                type="button"
                className="absolute right-2 top-2 text-xs text-[var(--muted)]"
                onClick={() => setShowKeys((v) => !v)}
              >
                {showKeys ? "скрыть" : "показать"}
              </button>
            </div>
            <button
              type="button"
              disabled={busy || !keysBlob.trim()}
              className="mt-4 border border-[var(--accent)] bg-[var(--accent)] px-4 py-2 text-sm text-[var(--btn-fg)] disabled:opacity-40"
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  await client.addKeys(provider, keysBlob, label || undefined);
                  setKeysBlob("");
                  setLabel("");
                  await loadKeys();
                } catch (e) {
                  setError(e instanceof Error ? e.message : "Ошибка");
                } finally {
                  setBusy(false);
                }
              }}
            >
              + Добавить в пул
            </button>
            <p className="mt-3 text-xs text-[var(--muted)]">
              {provider === "groq" ? (
                <a href="https://console.groq.com/keys" target="_blank" rel="noreferrer" className="text-[var(--accent)]">
                  console.groq.com → Keys
                </a>
              ) : (
                <a
                  href="https://aistudio.google.com/apikey"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[var(--accent)]"
                >
                  Google AI Studio → API key
                </a>
              )}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
