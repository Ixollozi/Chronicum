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

const TABS = [
  {
    id: "modes" as const,
    label: "Режимы",
    hint: "Глубина и авто",
    tip: "Как по умолчанию разбирать новые материалы: насколько подробно и нужно ли снимать кадры с видео.",
  },
  {
    id: "context" as const,
    label: "Контекст",
    hint: "Кто ты · что важно",
    tip: "Короткий профиль о тебе. По нему ИИ решает, насколько материал полезен именно тебе (оценка 1–10).",
  },
  {
    id: "keys" as const,
    label: "Ключи",
    hint: "Пулы AI",
    tip: "API-ключи для AI. Groq — основной (быстро), Gemini — запасной, если Groq недоступен.",
  },
];

const DEPTHS = [
  {
    id: "compact",
    title: "Compact",
    desc: "Только выжимка — быстро и коротко.",
    tip: "Экономит токены и время. Подходит, если материала много и достаточно краткой сути.",
  },
  {
    id: "standard",
    title: "Standard",
    desc: "Карточка + выжимка — баланс по умолчанию.",
    tip: "Обычный режим: заголовок, оценка, выжимка и базовая структура без лишней глубины.",
  },
  {
    id: "detailed",
    title: "Detailed",
    desc: "Сразу подробности и оценка.",
    tip: "Больше контента сразу: пояснения и более развёрнутый разбор. Дороже по AI и дольше.",
  },
];

const HORIZONS = [
  {
    id: "now",
    label: "Эта неделя",
    desc: "Срочное и тактическое",
    tip: "Выше ценятся советы, которые можно применить сразу. «На будущее» почти не поднимает score.",
  },
  {
    id: "90d",
    label: "≈ 90 дней",
    desc: "Текущий квартал",
    tip: "Баланс: полезно и то, что применимо сейчас, и то, что пригодится в ближайшие месяцы.",
  },
  {
    id: "long",
    label: "Долгосрок",
    desc: "Стратегия на годы",
    tip: "Стратегические и фундаментальные материалы получают больше баллов, даже если не «на завтра».",
  },
];

const CONTEXT_FIELDS = {
  role: {
    q: "Кем ты себя считаешь в работе?",
    help: "Роль или позиция — коротко",
    tip: "Например: «владелец агентства» или «продакт». ИИ понимает, с какой точки зрения смотреть на материал.",
  },
  goals: {
    q: "К каким результатам ты идёшь?",
    help: "2–5 целей · с новой строки",
    tip: "Зачем ты копил материалы. Если контент двигает к этим целям — оценка выше.",
  },
  focuses: {
    q: "Что для тебя сейчас «в теме»?",
    help: "Повышает оценку",
    tip: "Темы, которые обычно нужны. Совпадение с ними заметно поднимает personal fit и итоговый score.",
  },
  anti: {
    q: "Что почти всегда мимо?",
    help: "Понижает оценку",
    tip: "Темы, которые редко бывают полезны. Такие материалы получают ниже score, даже если сами по себе качественные.",
  },
  horizon: {
    q: "На какой срок смотреть полезность?",
    help: "Как сравнивать «сейчас» и «на потом»",
    tip: "От этого зависит, хвалить ли срочные хаки или длинные стратегии.",
  },
  priorities: {
    q: "Что важнее всего прямо сейчас?",
    help: "1–3 приоритета · сильнее всего влияют",
    tip: "Самый сильный сигнал. Материал, который бьёт в текущие приоритеты, почти всегда в топе оценки.",
  },
};

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
  const [modeFlash, setModeFlash] = useState<string | null>(null);

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
  const enabledCount = keys.filter((k) => k.enabled).length;

  async function patchMode(body: Partial<AppSettings>) {
    setError(null);
    try {
      const next = await client.patchSettings(body);
      setSettings(next);
      setModeFlash("Сохранено");
      window.setTimeout(() => setModeFlash(null), 1400);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить");
    }
  }

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

  const activeTab = TABS.find((t) => t.id === tab)!;

  return (
    <section className="settings-page animate-fade">
      <header className="settings-hero">
        <p className="settings-eyebrow">Параметры архива</p>
        <h1 className="settings-title" style={{ fontFamily: "var(--font-display)" }}>
          Настройки
        </h1>
        <div className="settings-title-rule" />
        <p className="settings-lede">
          {tab === "context"
            ? "Ответь коротко — ИИ будет оценивать материалы именно для тебя, а не «вообще»."
            : tab === "keys"
              ? "Сначала Groq, при исчерпании — Gemini. Ключи из UI или .env."
              : "Глубина разбора, автомоменты и порядок провайдеров."}
        </p>
      </header>

      <div className="settings-tabs" role="tablist" aria-label="Разделы настроек">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className="settings-tab settings-hint"
            data-active={tab === t.id}
            data-tip={t.tip}
            onClick={() => {
              setTab(t.id);
              setError(null);
              setCheckMsg(null);
            }}
          >
            <span className="settings-tab-label">{t.label}</span>
            <span className="settings-tab-hint">{t.hint}</span>
          </button>
        ))}
      </div>
      <p className="settings-inline-help md:hidden" aria-live="polite">
        {activeTab.tip || activeTab.hint}
      </p>

      {error && (
        <p className="settings-alert" role="alert">
          {error}
        </p>
      )}

      {!settings && tab === "modes" && (
        <div className="settings-skel">
          <div className="archive-skeleton h-24 w-full rounded-[var(--radius-xl)]" />
          <div className="archive-skeleton mt-3 h-24 w-full rounded-[var(--radius-xl)]" />
          <div className="archive-skeleton mt-3 h-16 w-full rounded-[var(--radius-xl)]" />
        </div>
      )}

      {tab === "modes" && settings && (
        <div className="settings-panel" role="tabpanel" aria-label={activeTab.label}>
          <div className="settings-section-head">
            <div>
              <p className="settings-kicker">Анализ</p>
              <h2
                className="settings-section-title settings-hint"
                data-tip="Выбери, насколько подробно Chronicum разбирает новые ссылки и заметки сразу после сохранения."
              >
                Глубина по умолчанию
              </h2>
            </div>
            {modeFlash && <span className="settings-flash">{modeFlash}</span>}
          </div>

          <div className="settings-depth-grid" role="radiogroup" aria-label="Глубина анализа">
            {DEPTHS.map((d) => (
              <button
                key={d.id}
                type="button"
                role="radio"
                aria-checked={settings.default_depth === d.id}
                className="settings-choice settings-hint"
                data-active={settings.default_depth === d.id}
                data-tip={d.tip}
                onClick={() => patchMode({ default_depth: d.id })}
              >
                <span className="settings-choice-title">{d.title}</span>
                <span className="settings-choice-desc">{d.desc}</span>
              </button>
            ))}
          </div>

          <div
            className="settings-toggle-row settings-hint"
            data-tip="Для YouTube / Instagram / видео ИИ выберет ключевые кадры и сохранит скриншоты. Можно выключить, если нужны только текст и выжимка."
          >
            <div>
              <p className="settings-field-label">Автоскриншоты</p>
              <p className="settings-field-help">Ключевые кадры для видео снимаются автоматически</p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={settings.auto_moments}
              className="settings-switch"
              data-on={settings.auto_moments}
              onClick={() => patchMode({ auto_moments: !settings.auto_moments })}
            >
              <span className="settings-switch-knob" />
            </button>
          </div>

          <div
            className="settings-info settings-hint"
            data-tip="В каком порядке Chronicum ходит в AI. Если первый провайдер исчерпал лимит или упал — берётся следующий."
          >
            <p className="settings-kicker">Провайдеры</p>
            <p className="settings-info-text">
              Порядок:{" "}
              <strong>{(settings.provider_order || []).join(" → ") || "не задан"}</strong>
            </p>
            <p className="settings-field-help mt-2">
              Личный профиль для оценки — во вкладке{" "}
              <button type="button" className="settings-inline-link" onClick={() => setTab("context")}>
                Контекст
              </button>
              .
            </p>
          </div>
        </div>
      )}

      {tab === "context" && (
        <div className="settings-panel" role="tabpanel" aria-label={activeTab.label}>
          <div
            className="settings-callout settings-hint"
            data-tip="Оценка 1–10 считается для тебя: personal fit смотрит на совпадение с целями, темами и приоритетами. Без профиля оценка «средняя», как для случайного читателя."
          >
            <p className="settings-kicker">Как это работает</p>
            <p className="settings-callout-text">
              Chronicum смотрит на материал глазами твоего профиля: что совпало с интересами —
              поднимает оценку, что мимо — опускает. Без ответов оценка нейтральная.
            </p>
          </div>

          <div className="settings-form">
            <label className="settings-field">
              <span className="settings-q settings-hint" data-tip={CONTEXT_FIELDS.role.tip}>
                <span className="settings-q-num">1</span>
                {CONTEXT_FIELDS.role.q}
              </span>
              <span className="settings-field-help">{CONTEXT_FIELDS.role.help}</span>
              <input
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="Например: владелец AI-агентства, продакт, маркетолог…"
                className="settings-input"
              />
            </label>

            <label className="settings-field">
              <span className="settings-q settings-hint" data-tip={CONTEXT_FIELDS.goals.tip}>
                <span className="settings-q-num">2</span>
                {CONTEXT_FIELDS.goals.q}
              </span>
              <span className="settings-field-help">{CONTEXT_FIELDS.goals.help}</span>
              <textarea
                value={goalsText}
                onChange={(e) => setGoalsText(e.target.value)}
                rows={3}
                placeholder={"Масштабировать delivery\nЗакрывать сделки\nСобрать личную базу знаний"}
                className="settings-textarea"
              />
            </label>

            <div className="settings-form-grid">
              <label className="settings-field">
                <span className="settings-q settings-hint" data-tip={CONTEXT_FIELDS.focuses.tip}>
                  <span className="settings-q-num">3</span>
                  {CONTEXT_FIELDS.focuses.q}
                </span>
                <span className="settings-field-help">{CONTEXT_FIELDS.focuses.help}</span>
                <textarea
                  value={focusesText}
                  onChange={(e) => setFocusesText(e.target.value)}
                  rows={4}
                  placeholder={"AI и LLM\nМонтаж Reels / CapCut\nДизайн\nПродажи агентства"}
                  className="settings-textarea"
                />
              </label>
              <label className="settings-field">
                <span className="settings-q settings-hint" data-tip={CONTEXT_FIELDS.anti.tip}>
                  <span className="settings-q-num">4</span>
                  {CONTEXT_FIELDS.anti.q}
                </span>
                <span className="settings-field-help">{CONTEXT_FIELDS.anti.help}</span>
                <textarea
                  value={antiText}
                  onChange={(e) => setAntiText(e.target.value)}
                  rows={4}
                  placeholder={"Крипта\nГеймдев\nСпорт"}
                  className="settings-textarea"
                />
              </label>
            </div>

            <div className="settings-field">
              <span className="settings-q settings-hint" data-tip={CONTEXT_FIELDS.horizon.tip}>
                <span className="settings-q-num">5</span>
                {CONTEXT_FIELDS.horizon.q}
              </span>
              <span className="settings-field-help">{CONTEXT_FIELDS.horizon.help}</span>
              <div className="settings-horizon" role="radiogroup" aria-label="Горизонт применения">
                {HORIZONS.map((h) => (
                  <button
                    key={h.id}
                    type="button"
                    role="radio"
                    aria-checked={horizon === h.id}
                    className="settings-choice compact settings-hint"
                    data-active={horizon === h.id}
                    data-tip={h.tip}
                    onClick={() => setHorizon(h.id)}
                  >
                    <span className="settings-choice-title">{h.label}</span>
                    <span className="settings-choice-desc">{h.desc}</span>
                  </button>
                ))}
              </div>
            </div>

            <label className="settings-field">
              <span className="settings-q settings-hint" data-tip={CONTEXT_FIELDS.priorities.tip}>
                <span className="settings-q-num">6</span>
                {CONTEXT_FIELDS.priorities.q}
              </span>
              <span className="settings-field-help">{CONTEXT_FIELDS.priorities.help}</span>
              <textarea
                value={priorities}
                onChange={(e) => setPriorities(e.target.value)}
                rows={3}
                placeholder="Сейчас важнее всего: упаковать офферы, ускорить монтаж Reels, наладить базу знаний…"
                className="settings-textarea"
              />
            </label>
          </div>

          <div className="settings-footer">
            <button
              type="button"
              disabled={busy}
              onClick={saveProfile}
              className="settings-btn primary"
            >
              {busy ? "Сохраняю…" : "Сохранить ответы"}
            </button>
            {profileSaved && <span className="settings-flash">{profileSaved}</span>}
            <p
              className="settings-footnote settings-hint"
              data-tip="Новый профиль влияет только на свежие разборы. Чтобы пересчитать старый материал, открой карточку и нажми «Повторить» или полную обработку."
            >
              Старые карточки сами не пересчитаются. Новый score — на свежих разборах или после
              «Повторить».
            </p>
          </div>
        </div>
      )}

      {tab === "keys" && (
        <div className="settings-panel" role="tabpanel" aria-label={activeTab.label}>
          <div className="settings-provider-grid" role="tablist" aria-label="Провайдер">
            <button
              type="button"
              className="settings-choice settings-hint"
              data-active={provider === "groq"}
              data-tip="Основной провайдер. Быстрые ответы, обычно идёт первым в очереди. Сюда добавляй ключи вида gsk_…"
              onClick={() => setProvider("groq")}
            >
              <span className="settings-choice-title">Groq</span>
              <span className="settings-choice-desc">Основной · быстро</span>
            </button>
            <button
              type="button"
              className="settings-choice settings-hint"
              data-active={provider === "google"}
              data-tip="Запасной провайдер. Используется, если Groq недоступен или все его ключи вышли из строя."
              onClick={() => setProvider("google")}
            >
              <span className="settings-choice-title">Gemini</span>
              <span className="settings-choice-desc">Fallback Google</span>
            </button>
          </div>

          <div className="settings-stats">
            <span
              className="settings-stat settings-hint"
              data-tip="Сколько ключей доступно для выбранного провайдера: и из UI (CRM), и из .env."
            >
              В пуле · {poolCount}
            </span>
            <span
              className="settings-stat settings-hint"
              data-tip="Сколько ключей сейчас включены и могут использоваться для запросов."
            >
              Активны · {enabledCount}
            </span>
            <span
              className="settings-stat settings-hint"
              data-tip="Ключи, добавленные здесь в интерфейсе. Их можно выключать и удалять. Ключи из .env — только для чтения."
            >
              CRM · {crmCount}
            </span>
          </div>

          <div className="settings-toolbar">
            <button
              type="button"
              disabled={busy}
              className="settings-btn primary settings-hint"
              data-tip="Пингует каждый ключ в пуле: сколько живых (OK) и сколько с ошибкой (fail)."
              onClick={async () => {
                setBusy(true);
                setCheckMsg(null);
                setError(null);
                try {
                  const r = await client.checkKeys(provider);
                  setCheckMsg(`OK ${r.ok} · fail ${r.failed}`);
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
            <button type="button" className="settings-btn" onClick={() => loadKeys()}>
              Обновить
            </button>
            {checkMsg && <span className="settings-flash">{checkMsg}</span>}
          </div>

          <ul className="settings-key-list">
            {keys.map((k) => (
              <li key={String(k.id)} className="settings-key-row" data-disabled={!k.enabled}>
                <div className="settings-key-main">
                  <div className="settings-key-topline">
                    <p className="settings-key-label">{k.label || "без подписи"}</p>
                    <span className="settings-key-badge" data-source={k.source}>
                      {k.source === "env" ? ".env" : "CRM"}
                    </span>
                    {!k.enabled && <span className="settings-key-badge warn">выкл</span>}
                  </div>
                  <p className="settings-key-meta">
                    ••••{k.hint}
                    {k.last_error ? ` · ${k.last_error.slice(0, 48)}` : ""}
                  </p>
                </div>
                <div className="settings-key-actions">
                  {k.source === "crm" && typeof k.id === "number" && (
                    <>
                      <button
                        type="button"
                        className="settings-btn ghost"
                        onClick={async () => {
                          await client.patchKey(k.id as number, { enabled: !k.enabled });
                          await loadKeys();
                        }}
                      >
                        {k.enabled ? "Выкл" : "Вкл"}
                      </button>
                      <button
                        type="button"
                        className="settings-btn danger"
                        onClick={async () => {
                          if (!window.confirm("Удалить ключ из пула?")) return;
                          await client.deleteKey(k.id as number);
                          await loadKeys();
                        }}
                      >
                        Удалить
                      </button>
                    </>
                  )}
                  {k.source === "env" && (
                    <span className="settings-key-env">только из окружения</span>
                  )}
                </div>
              </li>
            ))}
            {keys.length === 0 && (
              <li className="settings-empty">
                Пул пуст — добавь ключи ниже или пропиши в .env
              </li>
            )}
          </ul>

          <div className="settings-add">
            <div className="settings-section-head">
              <div>
                <p className="settings-kicker">Добавить</p>
                <h2
                  className="settings-section-title settings-hint"
                  data-tip="Можно вставить несколько ключей сразу — каждый с новой строки или через запятую. После сохранения полный ключ больше не показывается, только хвост ••••."
                >
                  Ключи {provider === "groq" ? "Groq" : "Gemini"}
                </h2>
              </div>
            </div>
            <p className="settings-field-help">
              Один или несколько через Enter / запятую. Полный ключ после сохранения не
              показывается.
            </p>

            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Подпись (необязательно)"
              className="settings-input mt-4"
            />

            <div className="settings-secret mt-3">
              <textarea
                value={keysBlob}
                onChange={(e) => setKeysBlob(e.target.value)}
                rows={3}
                placeholder={provider === "groq" ? "gsk_..." : "AIza..."}
                className="settings-textarea"
                style={showKeys ? undefined : ({ WebkitTextSecurity: "disc" } as React.CSSProperties)}
              />
              <button
                type="button"
                className="settings-secret-toggle"
                onClick={() => setShowKeys((v) => !v)}
              >
                {showKeys ? "Скрыть" : "Показать"}
              </button>
            </div>

            <div className="settings-toolbar mt-4">
              <button
                type="button"
                disabled={busy || !keysBlob.trim()}
                className="settings-btn primary"
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
              {provider === "groq" ? (
                <a
                  href="https://console.groq.com/keys"
                  target="_blank"
                  rel="noreferrer"
                  className="settings-inline-link"
                >
                  console.groq.com → Keys
                </a>
              ) : (
                <a
                  href="https://aistudio.google.com/apikey"
                  target="_blank"
                  rel="noreferrer"
                  className="settings-inline-link"
                >
                  Google AI Studio → API key
                </a>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
