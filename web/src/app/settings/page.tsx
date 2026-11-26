"use client";

import { useCallback, useEffect, useState } from "react";
import {
  clearToken,
  client,
  getToken,
  isAuthError,
  type ApiKeyItem,
  type AppSettings,
  type AuthMe,
  type CookiesStatus,
  type PersonalApiToken,
  type PersonalProfile,
  type TagItem,
  type TagStatusValue,
} from "@/lib/api";
import {
  IDE_RULE,
  apiOrigin,
  claudeCodeCommand,
  claudeDesktopSnippet,
  cursorMcpInstallLink,
  mcpHttpUrl,
  vscodeMcpInstallLink,
  vscodeMcpJsonSnippet,
  windsurfSnippet,
} from "@/lib/mcpConnect";

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
  {
    id: "tags" as const,
    label: "Теги",
    hint: "Модерация",
    tip: "Новые теги от ИИ сначала попадают на проверку. Одобренные остаются в архиве и переиспользуются, отклонённые не добавляются повторно.",
  },
  {
    id: "ide" as const,
    label: "IDE",
    hint: "Cursor · MCP",
    tip: "Токен MCP и подключение к Cursor / VS Code. Google OAuth в MCP не работает — только Personal API Token.",
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
    label: "Роль",
    help: "Кем ты себя считаешь в работе",
    tip: "Например: «владелец агентства» или «продакт». ИИ смотрит на материал с этой точки зрения.",
    placeholder: "Владелец AI-агентства, продакт, маркетолог…",
  },
  goals: {
    label: "Цели",
    help: "2–5 результатов · с новой строки",
    tip: "Зачем ты копил материалы. Если контент двигает к этим целям — оценка выше.",
    placeholder: "Масштабировать delivery\nЗакрывать сделки\nСобрать базу знаний",
  },
  focuses: {
    label: "В теме",
    help: "Поднимает оценку",
    tip: "Темы, которые обычно нужны. Совпадение заметно поднимает personal fit.",
    placeholder: "AI и LLM\nМонтаж Reels\nДизайн\nПродажи",
  },
  anti: {
    label: "Мимо",
    help: "Понижает оценку",
    tip: "Темы, которые редко бывают полезны — score ниже, даже если материал сам по себе сильный.",
    placeholder: "Крипта\nГеймдев\nСпорт",
  },
  horizon: {
    label: "Горизонт",
    help: "На какой срок смотреть полезность",
    tip: "От этого зависит, хвалить ли срочные хаки или длинные стратегии.",
  },
  priorities: {
    label: "Приоритеты",
    help: "1–3 пункта · самый сильный сигнал",
    tip: "Материал, который бьёт в текущие приоритеты, почти всегда в топе оценки.",
    placeholder: "Упаковать офферы, ускорить монтаж Reels, наладить базу знаний…",
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

function Tip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="settings-tip">
      <button
        type="button"
        className="settings-tip-btn"
        aria-expanded={open}
        aria-label={open ? "Скрыть подсказку" : "Показать подсказку"}
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {open && <span className="settings-tip-body">{text}</span>}
    </span>
  );
}

export default function SettingsPage() {
  const [tab, setTab] = useState<"modes" | "context" | "keys" | "tags" | "ide">("modes");
  const [provider, setProvider] = useState<"groq" | "google">("groq");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [tagStatus, setTagStatus] = useState<TagStatusValue>("pending");
  const [tags, setTags] = useState<TagItem[]>([]);
  const [pendingTagCount, setPendingTagCount] = useState<number>(0);
  const [tagFlash, setTagFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [label, setLabel] = useState("");
  const [keysBlob, setKeysBlob] = useState("");
  const [showKeys, setShowKeys] = useState(false);
  const [checkMsg, setCheckMsg] = useState<string | null>(null);
  const [profileSaved, setProfileSaved] = useState<string | null>(null);
  const [modeFlash, setModeFlash] = useState<string | null>(null);
  const [cookiesStatus, setCookiesStatus] = useState<CookiesStatus | null>(null);
  const [cookiesDraft, setCookiesDraft] = useState("");
  const [cookiesBusy, setCookiesBusy] = useState(false);
  const [cookiesFlash, setCookiesFlash] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [me, setMe] = useState<AuthMe | null>(null);
  const [mcpTokens, setMcpTokens] = useState<PersonalApiToken[]>([]);
  const [tokenLabel, setTokenLabel] = useState("Cursor MCP");
  const [freshToken, setFreshToken] = useState<string | null>(null);
  const [health, setHealth] = useState<{
    status: string;
    mode: string;
    ai_ready: boolean;
    mcp_http?: boolean;
  } | null>(null);

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

  const loadTags = useCallback(async () => {
    const [list, pendingList] = await Promise.all([
      client.listTags(tagStatus),
      tagStatus === "pending" ? Promise.resolve(null) : client.listTags("pending"),
    ]);
    setTags(list);
    setPendingTagCount(tagStatus === "pending" ? list.length : (pendingList?.length ?? 0));
  }, [tagStatus]);

  useEffect(() => {
    if (!getToken()) return;
    loadSettings().catch((e) => {
      if (isAuthError(e)) return;
      setError(e instanceof Error ? e.message : "Ошибка");
    });
    client
      .getCookiesStatus()
      .then(setCookiesStatus)
      .catch(() => undefined);
    client
      .listTags("pending")
      .then((list) => setPendingTagCount(list.length))
      .catch(() => undefined);
  }, [loadSettings]);

  const loadAccount = useCallback(async () => {
    const profile = await client.authMe();
    setMe(profile);
    if (profile.id) {
      setMcpTokens(await client.listAuthTokens());
    } else {
      setMcpTokens([]);
    }
  }, []);

  useEffect(() => {
    if (!getToken()) return;
    loadAccount().catch((e) => {
      if (isAuthError(e)) return;
      setError(e instanceof Error ? e.message : "Ошибка");
    });
  }, [loadAccount]);

  useEffect(() => {
    if (!getToken()) return;
    if (tab === "keys") {
      loadKeys().catch((e) => {
        if (isAuthError(e)) return;
        setError(e instanceof Error ? e.message : "Ошибка");
      });
    }
    if (tab === "tags") {
      loadTags().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
    }
    if (tab === "ide") {
      client
        .health()
        .then(setHealth)
        .catch(() => setHealth(null));
    }
  }, [tab, loadKeys, loadTags]);

  const crmCount = keys.filter((k) => k.source === "crm").length;
  const poolCount = keys.length;
  const enabledCount = keys.filter((k) => k.enabled).length;
  const tabIndex = TABS.findIndex((t) => t.id === tab);
  const activeTab = TABS[tabIndex]!;

  async function copyText(id: string, text: string) {
    await navigator.clipboard.writeText(text);
    setCopied(id);
    window.setTimeout(() => setCopied(null), 1600);
  }

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

  async function saveCookies(content: string) {
    if (!content.trim() || cookiesBusy) return;
    setCookiesBusy(true);
    setError(null);
    setCookiesFlash(null);
    try {
      const next = await client.uploadCookies(content);
      setCookiesStatus(next);
      setCookiesDraft("");
      setCookiesFlash(`Сохранено (${next.entries} записей)`);
      window.setTimeout(() => setCookiesFlash(null), 2600);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить cookies");
    } finally {
      setCookiesBusy(false);
    }
  }

  async function clearCookies() {
    if (cookiesBusy) return;
    setCookiesBusy(true);
    setError(null);
    try {
      const next = await client.deleteCookies();
      setCookiesStatus(next);
      setCookiesFlash("Cookies удалены");
      window.setTimeout(() => setCookiesFlash(null), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить cookies");
    } finally {
      setCookiesBusy(false);
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

  const mcpBearer = freshToken || getToken();

  const lede =
    tab === "context"
      ? "Короткие ответы — и ИИ оценивает материалы именно для тебя, а не «вообще»."
      : tab === "keys"
        ? "Сначала Groq, при исчерпании — Gemini. Ключи из интерфейса или .env."
        : tab === "tags"
          ? "Новые теги от ИИ ждут одобрения, чтобы словарь оставался чистым."
          : tab === "ide"
            ? "Сначала создай токен MCP, затем подключай IDE. Cursor ставится диплинком; User Rules IDE не умеет принимать с сайта — копируй текст."
            : "Глубина разбора, автомоменты и расшифровка видео по умолчанию.";

  return (
    <section className="settings-root animate-fade" data-tab={tab}>
      <div className="settings-atmos" aria-hidden="true" />

      {/* Desktop only: same slot as archive «Архив» */}
      <div className="settings-titlebar">
        <p className="settings-titlebar-eyebrow">Chronicum</p>
        <h1 className="settings-title" style={{ fontFamily: "var(--font-display)" }}>
          Настройки
        </h1>
      </div>

      <div className="settings-page">
        <header className="settings-hero">
          <div className="settings-folio">
            <span>Chronicum</span>
            <span className="settings-folio-dot" aria-hidden="true" />
            <span>Параметры</span>
            <button
              type="button"
              onClick={() => clearToken("logout")}
              className="ml-auto text-xs text-[var(--muted)] underline-offset-4 transition hover:text-[var(--ink)] hover:underline"
            >
              Выйти / сменить токен
            </button>
          </div>
          <h1
            className="settings-title settings-title--in-hero"
            style={{ fontFamily: "var(--font-display)" }}
          >
            Настройки
          </h1>
          <p className="settings-hero-sub" style={{ fontFamily: "var(--font-display)" }}>
            как архив думает <em>с тобой</em>
          </p>
          <div className="settings-title-rule" aria-hidden="true" />
          <p className="settings-lede" key={tab}>
            {lede}
          </p>
        </header>

        <div className="settings-account" aria-label="Аккаунт Google">
          {me ? (
            <>
              {me.picture_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={me.picture_url}
                  alt=""
                  width={44}
                  height={44}
                  className="settings-account-avatar"
                />
              ) : (
                <div className="settings-account-avatar settings-account-avatar--fallback" aria-hidden="true">
                  {(me.name || me.email || "?").slice(0, 1).toUpperCase()}
                </div>
              )}
              <div className="settings-account-main">
                <p className="settings-account-name">
                  {me.name || (me.is_admin ? "Admin / Dev" : "—")}
                </p>
                <p className="settings-account-email">
                  {me.email || (me.is_admin ? "WEB_API_TOKEN (bootstrap)" : "без email")}
                </p>
              </div>
              <div className="settings-account-actions">
                <button
                  type="button"
                  className="settings-btn"
                  onClick={() => {
                    setTab("ide");
                    setError(null);
                  }}
                >
                  {mcpTokens.length > 0
                    ? `MCP · ${mcpTokens.length}`
                    : "Токен MCP"}
                </button>
                <button
                  type="button"
                  className="settings-btn"
                  onClick={async () => {
                    await client.authLogout().catch(() => undefined);
                    clearToken();
                    window.location.href = "/";
                  }}
                >
                  Выйти
                </button>
              </div>
            </>
          ) : (
            <p className="settings-field-help" style={{ margin: 0 }}>
              Загрузка аккаунта…
            </p>
          )}
        </div>

      <div className="settings-chrome">
        <div
          className="settings-tabs"
          role="tablist"
          aria-label="Разделы настроек"
          style={{
            ["--tab-i" as string]: tabIndex,
            ["--tab-n" as string]: TABS.length,
          }}
        >
          <span className="settings-tabs-thumb" aria-hidden="true" />
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className="settings-tab"
              data-active={tab === t.id}
              onClick={() => {
                setTab(t.id);
                setError(null);
                setCheckMsg(null);
                setTagFlash(null);
              }}
            >
              <span className="settings-tab-label">{t.label}</span>
              <span className="settings-tab-hint">
                {t.id === "tags" && pendingTagCount > 0
                  ? `${pendingTagCount} на проверке`
                  : t.hint}
              </span>
            </button>
          ))}
        </div>
      </div>
      <p className="settings-inline-help" aria-live="polite" key={`help-${tab}`}>
        {activeTab.tip}
      </p>

      {error && (
        <p className="settings-alert" role="alert">
          {error}
        </p>
      )}

      {!settings && tab === "modes" && (
        <div className="settings-skel">
          <div className="archive-skeleton h-20 w-full rounded-[var(--radius-xl)]" />
          <div className="archive-skeleton mt-3 h-16 w-full rounded-[var(--radius-xl)]" />
          <div className="archive-skeleton mt-3 h-24 w-full rounded-[var(--radius-xl)]" />
        </div>
      )}

      {tab === "modes" && settings && (
        <div className="settings-panel" role="tabpanel" aria-label={activeTab.label}>
          <div className="settings-section-head">
            <div>
              <p className="settings-kicker">Анализ</p>
              <h2 className="settings-section-title">
                Глубина по умолчанию
                <Tip text="Насколько подробно Chronicum разбирает новые ссылки и заметки сразу после сохранения." />
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
                className="settings-choice settings-depth-choice"
                data-active={settings.default_depth === d.id}
                onClick={() => patchMode({ default_depth: d.id })}
              >
                <span className="settings-choice-mark" aria-hidden="true" />
                <span className="settings-choice-title">{d.title}</span>
                <span className="settings-choice-desc">{d.desc}</span>
              </button>
            ))}
          </div>
          <p className="settings-depth-note" aria-live="polite">
            {DEPTHS.find((d) => d.id === settings.default_depth)?.desc}
          </p>

          <div className="settings-toggle-row">
            <div>
              <p className="settings-field-label">
                Автоскриншоты
                <Tip text="Для YouTube / Instagram / TikTok / видео ИИ выберет ключевые кадры и сохранит скриншоты. Можно выключить, если нужны только текст и выжимка." />
              </p>
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

          <div className="settings-toggle-row">
            <div>
              <p className="settings-field-label">
                Видео целиком
                <Tip text="По умолчанию Whisper разбирает всё аудио, не первые 20%. Выключи, если длинные ролики жрут лимиты Groq — тогда снова preview и full по кнопке." />
              </p>
              <p className="settings-field-help">YouTube / Instagram / TikTok / файл — полная расшифровка</p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={settings.video_full !== false}
              className="settings-switch"
              data-on={settings.video_full !== false}
              onClick={() => patchMode({ video_full: settings.video_full === false })}
            >
              <span className="settings-switch-knob" />
            </button>
          </div>

          <div className="settings-info">
            <p className="settings-kicker">Cookies (Instagram · TikTok · YouTube)</p>
            <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
              <p className="settings-info-text">
                {cookiesStatus?.configured ? (
                  <>
                    Подключено ({cookiesStatus.source === "env" ? "из .env" : cookiesStatus.source === "browser" ? `браузер ${cookiesStatus.browser}` : "загружен файл"}
                    {cookiesStatus.entries > 0 ? ` · ${cookiesStatus.entries} записей` : ""})
                  </>
                ) : (
                  "Не настроены — приватные Reels / TikTok могут требовать авторизацию"
                )}
              </p>
              {cookiesFlash && <span className="settings-flash">{cookiesFlash}</span>}
            </div>
            {cookiesStatus && cookiesStatus.entries > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                {(["instagram", "tiktok", "youtube"] as const).map((plat) => (
                  <span
                    key={plat}
                    className={`rounded-full border px-2.5 py-0.5 ${
                      cookiesStatus.platforms?.[plat]
                        ? "border-[var(--accent)] text-[var(--accent)]"
                        : "border-[var(--line)] text-[var(--muted)]"
                    }`}
                  >
                    {plat}: {cookiesStatus.platforms?.[plat] ? "ok" : "нет"}
                  </span>
                ))}
              </div>
            )}
            <p className="settings-field-help mt-2">
              Экспортируй Netscape <code className="text-[var(--ink)]">cookies.txt</code> расширением «Get cookies.txt LOCALLY» и загрузи файл или вставь содержимое ниже.
            </p>
            {cookiesStatus?.writable !== false ? (
              <div className="mt-3 space-y-2.5">
                <textarea
                  value={cookiesDraft}
                  onChange={(e) => setCookiesDraft(e.target.value)}
                  rows={3}
                  placeholder="# Netscape HTTP Cookie File&#10;.instagram.com&#9;TRUE&#9;/&#9;TRUE&#9;…"
                  className="settings-textarea font-mono text-xs"
                  aria-label="Содержимое Netscape cookies.txt"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={cookiesBusy || !cookiesDraft.trim()}
                    onClick={() => saveCookies(cookiesDraft)}
                    className="card-btn card-btn-accent"
                  >
                    {cookiesBusy ? "Сохраняю…" : "Сохранить cookies"}
                  </button>
                  <label className="card-btn cursor-pointer">
                    Загрузить .txt
                    <input
                      type="file"
                      accept=".txt,text/plain"
                      className="hidden"
                      onChange={async (e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        if (!f) return;
                        const text = await f.text();
                        await saveCookies(text);
                      }}
                    />
                  </label>
                  {cookiesStatus?.source === "managed" && (
                    <button
                      type="button"
                      disabled={cookiesBusy}
                      onClick={clearCookies}
                      className="card-btn card-btn-danger"
                    >
                      Удалить
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <p className="settings-field-help mt-2">
                Сейчас используется <code className="text-[var(--ink)]">MEDIA_COOKIES_FILE</code> из окружения (управление файлом через сервер).
              </p>
            )}
          </div>

          <div className="settings-info">
            <p className="settings-kicker">Провайдеры</p>
            <p className="settings-info-text">
              Порядок:{" "}
              <strong className="settings-provider-chain">
                {(settings.provider_order || []).join(" → ") || "не задан"}
              </strong>
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
          <div className="settings-bento">
            <article className="settings-bento-tile" data-tile="role">
              <header className="settings-bento-head">
                <p className="settings-bento-kicker">О тебе</p>
                <h2 className="settings-bento-title">
                  {CONTEXT_FIELDS.role.label}
                  <Tip text={CONTEXT_FIELDS.role.tip} />
                </h2>
                <p className="settings-bento-help">{CONTEXT_FIELDS.role.help}</p>
              </header>
              <input
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder={CONTEXT_FIELDS.role.placeholder}
                className="settings-input"
              />
            </article>

            <article className="settings-bento-tile" data-tile="horizon">
              <header className="settings-bento-head">
                <p className="settings-bento-kicker">Срок</p>
                <h2 className="settings-bento-title">
                  {CONTEXT_FIELDS.horizon.label}
                  <Tip text={CONTEXT_FIELDS.horizon.tip} />
                </h2>
                <p className="settings-bento-help">{CONTEXT_FIELDS.horizon.help}</p>
              </header>
              <div className="settings-bento-horizon" role="radiogroup" aria-label="Горизонт применения">
                {HORIZONS.map((h) => (
                  <button
                    key={h.id}
                    type="button"
                    role="radio"
                    aria-checked={horizon === h.id}
                    className="settings-bento-hz"
                    data-active={horizon === h.id}
                    onClick={() => setHorizon(h.id)}
                  >
                    <span className="settings-bento-hz-label">{h.label}</span>
                    <span className="settings-bento-hz-desc">{h.desc}</span>
                  </button>
                ))}
              </div>
            </article>

            <article className="settings-bento-tile" data-tile="goals">
              <header className="settings-bento-head">
                <p className="settings-bento-kicker">Направление</p>
                <h2 className="settings-bento-title">
                  {CONTEXT_FIELDS.goals.label}
                  <Tip text={CONTEXT_FIELDS.goals.tip} />
                </h2>
                <p className="settings-bento-help">{CONTEXT_FIELDS.goals.help}</p>
              </header>
              <textarea
                value={goalsText}
                onChange={(e) => setGoalsText(e.target.value)}
                rows={4}
                placeholder={CONTEXT_FIELDS.goals.placeholder}
                className="settings-textarea"
              />
            </article>

            <article className="settings-bento-tile" data-tile="focus">
              <header className="settings-bento-head">
                <p className="settings-bento-kicker">Фокус</p>
                <h2 className="settings-bento-title">
                  {CONTEXT_FIELDS.focuses.label}
                  <Tip text={CONTEXT_FIELDS.focuses.tip} />
                </h2>
                <p className="settings-bento-help">{CONTEXT_FIELDS.focuses.help}</p>
              </header>
              <textarea
                value={focusesText}
                onChange={(e) => setFocusesText(e.target.value)}
                rows={5}
                placeholder={CONTEXT_FIELDS.focuses.placeholder}
                className="settings-textarea"
              />
            </article>

            <article className="settings-bento-tile" data-tile="anti">
              <header className="settings-bento-head">
                <p className="settings-bento-kicker">Фильтр</p>
                <h2 className="settings-bento-title">
                  {CONTEXT_FIELDS.anti.label}
                  <Tip text={CONTEXT_FIELDS.anti.tip} />
                </h2>
                <p className="settings-bento-help">{CONTEXT_FIELDS.anti.help}</p>
              </header>
              <textarea
                value={antiText}
                onChange={(e) => setAntiText(e.target.value)}
                rows={5}
                placeholder={CONTEXT_FIELDS.anti.placeholder}
                className="settings-textarea"
              />
            </article>

            <article className="settings-bento-tile settings-bento-tile--accent" data-tile="now">
              <header className="settings-bento-head">
                <p className="settings-bento-kicker">Сейчас</p>
                <h2 className="settings-bento-title">
                  {CONTEXT_FIELDS.priorities.label}
                  <Tip text={CONTEXT_FIELDS.priorities.tip} />
                </h2>
                <p className="settings-bento-help">{CONTEXT_FIELDS.priorities.help}</p>
              </header>
              <textarea
                value={priorities}
                onChange={(e) => setPriorities(e.target.value)}
                rows={3}
                placeholder={CONTEXT_FIELDS.priorities.placeholder}
                className="settings-textarea"
              />
            </article>
          </div>

          <div className="settings-footer">
            <button
              type="button"
              disabled={busy}
              onClick={saveProfile}
              className="settings-btn primary"
            >
              {busy ? "Сохраняю…" : "Сохранить"}
            </button>
            {profileSaved && <span className="settings-flash">{profileSaved}</span>}
            <p className="settings-footnote">
              Новые ответы влияют на свежие разборы. Старые карточки — через «Повторить».
            </p>
          </div>
        </div>
      )}

      {tab === "keys" && (
        <div className="settings-panel" role="tabpanel" aria-label={activeTab.label}>
          <div className="settings-keys-bar">
            <div
              className="settings-seg settings-seg--provider"
              role="tablist"
              aria-label="Провайдер"
              style={{ ["--seg-i" as string]: provider === "groq" ? 0 : 1 }}
            >
              <span className="settings-seg-thumb" aria-hidden="true" />
              <button
                type="button"
                role="tab"
                aria-selected={provider === "groq"}
                className="settings-seg-item"
                data-active={provider === "groq"}
                onClick={() => setProvider("groq")}
              >
                Groq
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={provider === "google"}
                className="settings-seg-item"
                data-active={provider === "google"}
                onClick={() => setProvider("google")}
              >
                Gemini
              </button>
            </div>
          </div>

          <p className="settings-keys-lead">
            {provider === "groq"
              ? "Основной провайдер — быстрые ответы. Ключи вида gsk_…"
              : "Запасной провайдер, если Groq недоступен. Ключи из Google AI Studio."}
          </p>

          <div className="settings-keys-actions">
            <button
              type="button"
              disabled={busy}
              className="settings-btn primary"
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
              Проверить
            </button>
            <button type="button" className="settings-btn" onClick={() => loadKeys()}>
              Обновить
            </button>
            {checkMsg && <span className="settings-flash">{checkMsg}</span>}
            {provider === "groq" ? (
              <a
                href="https://console.groq.com/keys"
                target="_blank"
                rel="noreferrer"
                className="settings-inline-link settings-keys-ext"
              >
                Получить ключ →
              </a>
            ) : (
              <a
                href="https://aistudio.google.com/apikey"
                target="_blank"
                rel="noreferrer"
                className="settings-inline-link settings-keys-ext"
              >
                Получить ключ →
              </a>
            )}
          </div>

          <p className="settings-keys-meta">
            <span>{poolCount} в пуле</span>
            <span aria-hidden="true">·</span>
            <span>{enabledCount} акт.</span>
            <span aria-hidden="true">·</span>
            <span>{crmCount} CRM</span>
          </p>

          <ul className="settings-key-list">
            {keys.map((k) => (
              <li key={String(k.id)} className="settings-key-row" data-disabled={!k.enabled}>
                <div className="settings-key-main">
                  <div className="settings-key-topline">
                    <p className="settings-key-label">{k.label || "без подписи"}</p>
                    <span className="settings-key-badge" data-source={k.source}>
                      {k.source === "env" ? ".env" : "CRM"}
                    </span>
                  </div>
                  <p className="settings-key-meta">
                    <span className="settings-key-mono">••••{k.hint}</span>
                  </p>
                  {k.last_error && (
                    <p className="settings-key-error">{k.last_error.slice(0, 72)}</p>
                  )}
                  {k.source === "env" && (
                    <p className="settings-key-env">Только из окружения — выключить нельзя</p>
                  )}
                </div>
                <div className="settings-key-actions">
                  {k.source === "crm" && typeof k.id === "number" && (
                    <>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={k.enabled}
                        aria-label={k.enabled ? "Выключить ключ" : "Включить ключ"}
                        className="settings-switch settings-switch--sm"
                        data-on={k.enabled}
                        onClick={async () => {
                          await client.patchKey(k.id as number, { enabled: !k.enabled });
                          await loadKeys();
                        }}
                      >
                        <span className="settings-switch-knob" />
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
                </div>
              </li>
            ))}
            {keys.length === 0 && (
              <li className="settings-empty">
                <p className="settings-empty-title" style={{ fontFamily: "var(--font-display)" }}>
                  Пул пуст
                </p>
                <p className="settings-empty-desc">Добавь ключ ниже или пропиши в .env</p>
              </li>
            )}
          </ul>

          <div className="settings-add">
            <div className="settings-section-head">
              <div>
                <p className="settings-kicker">Новый ключ</p>
                <h2 className="settings-section-title">
                  {provider === "groq" ? "Groq" : "Gemini"}
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
              autoComplete="off"
            />

            <div className="settings-secret mt-3">
              <textarea
                value={keysBlob}
                onChange={(e) => setKeysBlob(e.target.value)}
                rows={3}
                placeholder={provider === "groq" ? "gsk_..." : "AIza..."}
                className="settings-textarea"
                autoComplete="off"
                spellCheck={false}
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
                Добавить в пул
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === "tags" && (
        <div className="settings-panel settings-keys" key="tags">
          <div className="settings-keys-head">
            <div>
              <p className="settings-kicker">Словарь тегов</p>
              <h2 className="settings-section-title">Модерация тегов</h2>
            </div>
            <div className="settings-seg" role="tablist" aria-label="Статус тегов">
              <button
                type="button"
                role="tab"
                aria-selected={tagStatus === "pending"}
                className="settings-seg-item"
                data-active={tagStatus === "pending"}
                onClick={() => {
                  setTagStatus("pending");
                  setTagFlash(null);
                }}
              >
                На проверке{pendingTagCount > 0 ? ` (${pendingTagCount})` : ""}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tagStatus === "approved"}
                className="settings-seg-item"
                data-active={tagStatus === "approved"}
                onClick={() => {
                  setTagStatus("approved");
                  setTagFlash(null);
                }}
              >
                Одобренные
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tagStatus === "rejected"}
                className="settings-seg-item"
                data-active={tagStatus === "rejected"}
                onClick={() => {
                  setTagStatus("rejected");
                  setTagFlash(null);
                }}
              >
                Отклонённые
              </button>
            </div>
          </div>

          <p className="settings-keys-lead">
            {tagStatus === "pending"
              ? "Теги, предложенные ИИ при разборе материалов. В карточках они отмечены знаком «?»."
              : tagStatus === "approved"
                ? "Подтверждённые теги — ИИ переиспользует их автоматически."
                : "Отклонённые теги больше не привязываются к новым материалам."}
          </p>

          <div className="settings-keys-actions">
            {tagStatus === "pending" && tags.length > 0 && (
              <button
                type="button"
                disabled={busy}
                className="settings-btn primary"
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  setTagFlash(null);
                  try {
                    for (const t of tags) {
                      await client.approveTag(t.id);
                    }
                    setTagFlash(`Одобрено: ${tags.length}`);
                    await loadTags();
                  } catch (e) {
                    setError(e instanceof Error ? e.message : "Ошибка одобрения");
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Одобрить все ({tags.length})
              </button>
            )}
            <button
              type="button"
              className="settings-btn"
              disabled={busy}
              onClick={() => {
                setTagFlash(null);
                loadTags().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
              }}
            >
              Обновить
            </button>
            {tagFlash && <span className="settings-flash">{tagFlash}</span>}
          </div>

          <ul className="settings-key-list">
            {tags.map((t) => (
              <li
                key={t.id}
                className="settings-key-row"
                data-disabled={t.status === "rejected"}
              >
                <div className="settings-key-main">
                  <div className="settings-key-topline">
                    <p className="settings-key-label">#{t.name}</p>
                    <span className="settings-key-badge">
                      {t.status === "pending"
                        ? "на проверке"
                        : t.status === "approved"
                          ? "одобрен"
                          : "отклонён"}
                    </span>
                  </div>
                </div>
                <div className="settings-key-actions">
                  {t.status !== "approved" && (
                    <button
                      type="button"
                      disabled={busy}
                      className="settings-btn primary"
                      onClick={async () => {
                        setBusy(true);
                        setError(null);
                        setTagFlash(null);
                        try {
                          await client.approveTag(t.id);
                          setTagFlash(`Тег #${t.name} одобрен`);
                          await loadTags();
                        } catch (e) {
                          setError(e instanceof Error ? e.message : "Ошибка одобрения");
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Одобрить
                    </button>
                  )}
                  {t.status !== "rejected" && (
                    <button
                      type="button"
                      disabled={busy}
                      className="settings-btn danger"
                      onClick={async () => {
                        setBusy(true);
                        setError(null);
                        setTagFlash(null);
                        try {
                          await client.rejectTag(t.id);
                          setTagFlash(`Тег #${t.name} отклонён`);
                          await loadTags();
                        } catch (e) {
                          setError(e instanceof Error ? e.message : "Ошибка отклонения");
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Отклонить
                    </button>
                  )}
                </div>
              </li>
            ))}
            {tags.length === 0 && (
              <li className="settings-empty">
                <p className="settings-empty-title" style={{ fontFamily: "var(--font-display)" }}>
                  {tagStatus === "pending"
                    ? "Нет тегов на проверке"
                    : tagStatus === "approved"
                      ? "Одобренных тегов пока нет"
                      : "Отклонённых тегов нет"}
                </p>
                <p className="settings-empty-desc">
                  {tagStatus === "pending"
                    ? "Когда ИИ предложит новый тег при разборе материала, он появится здесь."
                    : "Список обновится после разбора и модерации материалов."}
                </p>
              </li>
            )}
          </ul>
        </div>
      )}

      {tab === "ide" && (
        <div className="settings-panel" role="tabpanel" aria-label={activeTab.label}>
          <div className="settings-section-head">
            <div>
              <p className="settings-kicker">1 · Доступ</p>
              <h2 className="settings-section-title">
                Personal API Token
                <Tip text="Cursor и Claude принимают только Bearer из конфига. Google OAuth в MCP не открывается." />
              </h2>
            </div>
          </div>

          {!me?.id ? (
            <p className="settings-field-help">
              Токены MCP привязаны к Google-аккаунту сверху.
            </p>
          ) : (
            <>
              <div className="settings-add">
                <input
                  value={tokenLabel}
                  onChange={(e) => setTokenLabel(e.target.value)}
                  placeholder="Подпись (Cursor MCP)"
                  className="settings-input"
                  autoComplete="off"
                />
                <div className="settings-toolbar mt-3">
                  <button
                    type="button"
                    disabled={busy}
                    className="settings-btn primary"
                    onClick={async () => {
                      setBusy(true);
                      setError(null);
                      try {
                        const created = await client.createAuthToken(tokenLabel || undefined);
                        setFreshToken(created.token);
                        setMcpTokens(await client.listAuthTokens());
                      } catch (e) {
                        setError(e instanceof Error ? e.message : "Ошибка");
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Создать токен
                  </button>
                </div>
              </div>
              {freshToken && (
                <div className="settings-info mt-4">
                  <p className="settings-kicker">Покажи один раз</p>
                  <pre className="settings-ide-rule break-all whitespace-pre-wrap">{freshToken}</pre>
                  <div className="settings-ide-actions mt-3">
                    <button
                      type="button"
                      className="settings-btn primary"
                      onClick={() => copyText("pat", freshToken)}
                    >
                      {copied === "pat" ? "Скопировано" : "Скопировать токен"}
                    </button>
                    <a className="settings-btn primary" href={cursorMcpInstallLink(freshToken)}>
                      Добавить в Cursor
                    </a>
                  </div>
                  <p className="settings-field-help mt-2">
                    Сохрани токен — повторно его не показать. Потом можно отозвать.
                  </p>
                </div>
              )}
              <ul className="settings-key-list mt-4">
                {mcpTokens.map((t) => (
                  <li key={t.id} className="settings-key-row">
                    <div>
                      <p className="settings-info-text">
                        <strong>{t.label || "MCP token"}</strong>{" "}
                        <span className="settings-key-mono">{t.token_prefix}…</span>
                      </p>
                      <p className="settings-field-help">
                        создан {t.created_at ? new Date(t.created_at).toLocaleString() : "—"}
                        {t.last_used_at
                          ? ` · last ${new Date(t.last_used_at).toLocaleString()}`
                          : ""}
                      </p>
                    </div>
                    <button
                      type="button"
                      className="settings-btn danger"
                      onClick={async () => {
                        if (!window.confirm("Отозвать токен?")) return;
                        await client.revokeAuthToken(t.id);
                        if (freshToken?.startsWith(t.token_prefix)) setFreshToken(null);
                        setMcpTokens(await client.listAuthTokens());
                      }}
                    >
                      Отозвать
                    </button>
                  </li>
                ))}
                {mcpTokens.length === 0 && (
                  <li className="settings-empty">
                    <p className="settings-empty-title" style={{ fontFamily: "var(--font-display)" }}>
                      Токенов нет
                    </p>
                    <p className="settings-empty-desc">Создай Personal API Token для Cursor MCP</p>
                  </li>
                )}
              </ul>
            </>
          )}

          <div className="settings-section-head mt-8">
            <div>
              <p className="settings-kicker">2 · Подключение</p>
              <h2 className="settings-section-title">
                Добавить в IDE
                <Tip text="Cursor откроет диалог установки MCP. Подтверди его. Сайт не может тихо прописать конфиг или User Rules." />
              </h2>
            </div>
          </div>

          <div className="settings-info">
            <p className="settings-kicker">Статус</p>
            <p className="settings-info-text">
              API:{" "}
              <strong>
                {health ? `${health.status} · ${health.mode}` : "не отвечает"}
              </strong>
              {health ? ` · AI ${health.ai_ready ? "готов" : "без ключей"}` : ""}
            </p>
            <p className="settings-field-help mt-2">
              MCP HTTP:{" "}
              {health?.mcp_http
                ? `${mcpHttpUrl()} — тот же процесс, что и кабинет`
                : "не смонтирован (поставь пакет mcp в venv и перезапусти API)"}
            </p>
          </div>

          <p className="settings-field-help mb-3">
            В кнопках ниже —{" "}
            {freshToken ? "свежий PAT" : mcpTokens.length > 0 ? "сессия Google (лучше создай PAT)" : "сессия"}.
            Для Cursor надёжнее Personal API Token сверху.
          </p>
          <div className="settings-ide-actions">
            <a className="settings-btn primary" href={cursorMcpInstallLink(mcpBearer)}>
              Добавить в Cursor
            </a>
            <a className="settings-btn primary" href={vscodeMcpInstallLink(mcpBearer)}>
              Добавить в VS Code
            </a>
            <a className="settings-btn" href={vscodeMcpInstallLink(mcpBearer, apiOrigin(), "vscode-insiders")}>
              VS Code Insiders
            </a>
          </div>
          <p className="settings-field-help mt-3">
            Эти три кнопки открывают IDE. Нужно подтвердить диалог. API должен быть запущен.
          </p>

          <div className="settings-ide-actions">
            <button
              type="button"
              className="settings-btn"
              onClick={() => copyText("rule", IDE_RULE)}
            >
              {copied === "rule" ? "Правило скопировано" : "Скопировать правило"}
            </button>
            <button
              type="button"
              className="settings-btn"
              onClick={() => copyText("claude", claudeDesktopSnippet(mcpBearer))}
            >
              {copied === "claude" ? "JSON скопирован" : "Claude Desktop"}
            </button>
            <button
              type="button"
              className="settings-btn"
              onClick={() => copyText("code", claudeCodeCommand(mcpBearer))}
            >
              {copied === "code" ? "Команда скопирована" : "Claude Code CLI"}
            </button>
            <button
              type="button"
              className="settings-btn"
              onClick={() => copyText("windsurf", windsurfSnippet(mcpBearer))}
            >
              {copied === "windsurf" ? "JSON скопирован" : "Windsurf"}
            </button>
            <button
              type="button"
              className="settings-btn"
              onClick={() => copyText("vscodejson", vscodeMcpJsonSnippet(mcpBearer))}
            >
              {copied === "vscodejson" ? "JSON скопирован" : "VS Code mcp.json"}
            </button>
          </div>

          <p className="settings-field-help mt-4">
            Claude Desktop / Windsurf: вставь JSON в их mcp-конфиг. Claude Code: выполни команду в
            терминале. Правило — в User Rules IDE, если хочешь жёстче instructions MCP.
          </p>
          <pre className="settings-ide-rule">{IDE_RULE}</pre>
        </div>
      )}
      </div>
    </section>
  );
}
