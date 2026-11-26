"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { BrandMark } from "@/components/BrandMark";
import {
  AUTH_CHANGED_EVENT,
  clearToken,
  client,
  getToken,
  googleLoginUrl,
  isGoogleSession,
  setToken,
  verifyToken,
} from "@/lib/api";

const NAV = [
  { href: "/", label: "Главная", icon: "home" as const },
  { href: "/graph", label: "Карта", icon: "graph" as const },
  { href: "/archive", label: "Архив", icon: "archive" as const },
  { href: "/clients", label: "Клиенты", icon: "clients" as const },
  { href: "/settings", label: "Ещё", icon: "more" as const },
];

function NavIcon({ name, active }: { name: (typeof NAV)[number]["icon"]; active: boolean }) {
  const stroke = active ? "var(--accent)" : "currentColor";
  const common = {
    width: 22,
    height: 22,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke,
    strokeWidth: 1.65,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
  };

  switch (name) {
    case "home":
      return (
        <svg {...common}>
          <path d="M4.5 10.5 12 4l7.5 6.5V20a1 1 0 0 1-1 1h-4.5v-5.5h-4V21H5.5a1 1 0 0 1-1-1v-9.5Z" />
        </svg>
      );
    case "graph":
      return (
        <svg {...common}>
          <circle cx="6.5" cy="7" r="2.2" />
          <circle cx="17.5" cy="6.5" r="2.2" />
          <circle cx="12" cy="17.5" r="2.4" />
          <path d="M8.4 8.2 10.6 15.2M15.8 8l-2.2 7.2M8.7 7.2h6.6" />
        </svg>
      );
    case "archive":
      return (
        <svg {...common}>
          <path d="M4.5 7.5h15v11a1.5 1.5 0 0 1-1.5 1.5h-12a1.5 1.5 0 0 1-1.5-1.5v-11Z" />
          <path d="M3.5 5.5h17v2h-17z" />
          <path d="M10 12.5h4" />
        </svg>
      );
    case "clients":
      return (
        <svg {...common}>
          <circle cx="9" cy="8" r="2.4" />
          <circle cx="16" cy="9" r="2" />
          <path d="M4.5 18.5c.8-3 2.8-4.5 4.5-4.5s3.7 1.5 4.5 4.5" />
          <path d="M13.2 18.2c.5-1.8 1.7-2.9 2.8-2.9 1.3 0 2.3.9 2.9 2.4" />
        </svg>
      );
    case "more":
      return (
        <svg {...common}>
          <circle cx="12" cy="6.5" r="1.15" fill={stroke} stroke="none" />
          <circle cx="12" cy="12" r="1.15" fill={stroke} stroke="none" />
          <circle cx="12" cy="17.5" r="1.15" fill={stroke} stroke="none" />
        </svg>
      );
  }
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [tokenInput, setTokenInput] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [showTokenForm, setShowTokenForm] = useState(false);
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [googleReady, setGoogleReady] = useState<boolean | null>(null);
  const isAuthCallback = pathname.startsWith("/auth/callback");

  // Validate session once when entering the app — not on every route change
  // (re-checking on pathname ate the first click / briefly remounted the shell).
  useEffect(() => {
    if (isAuthCallback) {
      setReady(true);
      return;
    }

    let cancelled = false;
    (async () => {
      const base = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8010";
      let gReady = false;
      try {
        const res = await fetch(`${base.replace(/\/$/, "")}/api/auth/status`);
        const d = await res.json();
        gReady = Boolean(d?.google_oauth_ready);
      } catch {
        gReady = false;
      }
      if (!cancelled) setGoogleReady(gReady);

      const token = getToken();
      if (!token) {
        if (!cancelled) {
          setAuthed(false);
          setReady(true);
        }
        return;
      }
      try {
        const me = await client.authMe();
        if (cancelled) return;
        if (isGoogleSession(me) || !gReady) {
          setAuthed(true);
        } else {
          // Admin/dev bootstrap or stale token while Google OAuth is active
          clearToken();
          setAuthed(false);
        }
      } catch {
        if (!cancelled) {
          clearToken();
          setAuthed(false);
        }
      } finally {
        if (!cancelled) setReady(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [isAuthCallback]);

  useEffect(() => {
    const sync = (ev?: Event) => {
      const t = getToken();
      if (!t) {
        setAuthed(false);
      }
      const reason = (ev as CustomEvent<{ reason?: string }> | undefined)?.detail?.reason;
      if (reason === "unauthorized") {
        setAuthError("Сессия истекла или токен отклонён сервером (401). Войдите снова.");
      } else if (reason === "logout") {
        setAuthError(null);
      }
      setReady(true);
    };
    window.addEventListener(AUTH_CHANGED_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(AUTH_CHANGED_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  async function onLogin(e: React.FormEvent) {
    e.preventDefault();
    if (authBusy) return;
    const candidate = tokenInput.trim() || "chronicum-dev-token";
    setAuthBusy(true);
    setAuthError(null);
    try {
      await verifyToken(candidate);
      setToken(candidate);
      setTokenInput("");
      setAuthed(true);
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Не удалось проверить токен");
    } finally {
      setAuthBusy(false);
    }
  }

  const hasSessionToken = typeof window !== "undefined" && Boolean(getToken());

  if (!ready || (!authed && hasSessionToken && !isAuthCallback)) {
    return (
      <main className="auth-gate">
        <div className="auth-gate-glow" aria-hidden="true" />
        <div className="auth-gate-stack">
          <BrandMark size={48} className="auth-gate-mark" priority />
          <p className="auth-gate-loading animate-fade">Chronicum</p>
        </div>
      </main>
    );
  }

  if (isAuthCallback) {
    return <>{children}</>;
  }

  if (!authed) {
    const tokenFormVisible = googleReady === false || showTokenForm;
    return (
      <main className="auth-gate">
        <div className="auth-gate-glow" aria-hidden="true" />
        <div className="auth-gate-stack">
          <BrandMark size={56} className="auth-gate-mark" priority />
          <p className="auth-gate-kicker animate-fade">Personal knowledge</p>
          <h1 className="auth-gate-title animate-rise">Chronicum</h1>
          <p className="auth-gate-lede animate-rise" style={{ animationDelay: "80ms" }}>
            {googleReady === false
              ? "Одна строка — спросить, сохранить ссылку или заметку. Сеть — увидеть связи."
              : "Войди через Google — архив привязан к аккаунту. Без регистрации кабинет закрыт."}
          </p>
          {authError && (
            <p className="mt-3 text-sm text-[var(--danger,#e05d5d)]" role="alert">
              {authError}
            </p>
          )}
          <div className="auth-gate-actions animate-rise" style={{ animationDelay: "140ms" }}>
            {googleReady !== false && (
              <a
                href={googleReady === true ? googleLoginUrl() : undefined}
                aria-disabled={googleReady !== true}
                onClick={(e) => {
                  if (googleReady !== true) e.preventDefault();
                }}
                className="auth-gate-google"
              >
                <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
                  <path
                    fill="#FFC107"
                    d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34.2 6.1 29.4 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.5-.4-3.5z"
                  />
                  <path
                    fill="#FF3D00"
                    d="M6.3 14.7l6.6 4.8C14.7 16 19 13 24 13c3 0 5.8 1.1 7.9 3l5.7-5.7C34.2 6.1 29.4 4 24 4 16.3 4 9.6 8.3 6.3 14.7z"
                  />
                  <path
                    fill="#4CAF50"
                    d="M24 44c5.2 0 10-2 13.6-5.2l-6.3-5.3C29.3 35.1 26.8 36 24 36c-5.3 0-9.7-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"
                  />
                  <path
                    fill="#1976D2"
                    d="M43.6 20.5H42V20H24v8h11.3c-1.1 3.1-3.6 5.5-6.7 6.8l.1.1 6.3 5.3C38.1 37.5 44 32 44 24c0-1.3-.1-2.5-.4-3.5z"
                  />
                </svg>
                Continue with Google
              </a>
            )}
            {googleReady === true && !showTokenForm && (
              <button
                type="button"
                onClick={() => setShowTokenForm(true)}
                className="mt-3 text-xs text-[var(--muted)] underline-offset-4 transition hover:text-[var(--ink)] hover:underline"
              >
                Войти по API-токену
              </button>
            )}
            {tokenFormVisible && (
              <form className="mt-5 w-full space-y-4 text-left" onSubmit={onLogin}>
                <label className="block text-sm text-[var(--muted)]">
                  API-токен (PAT или WEB_API_TOKEN)
                  <input
                    type="password"
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    placeholder="chr_... или WEB_API_TOKEN"
                    autoComplete="current-password"
                    className="mt-2 w-full border-b border-[var(--line)] bg-transparent px-0 py-3 text-base outline-none transition focus:border-[var(--accent)]"
                  />
                </label>
                <button
                  type="submit"
                  disabled={authBusy}
                  className="w-full rounded-[var(--radius-pill)] border border-[var(--accent)] bg-[var(--accent)] px-5 py-3 text-sm text-[var(--btn-fg)] transition hover:brightness-110 disabled:opacity-60"
                >
                  {authBusy ? "Проверяю…" : "Войти"}
                </button>
              </form>
            )}
          </div>
        </div>
      </main>
    );
  }

  const isGraph = pathname === "/graph";
  const isArchive = pathname === "/archive";
  const isSettings = pathname === "/settings";
  const isMaterial = pathname.startsWith("/m/");

  return (
    <div
      className={
        isGraph
          ? "app-shell app-shell--graph relative z-10 min-h-[100dvh]"
          : isArchive || isSettings
            ? "app-shell app-shell--archive relative z-10 mx-auto min-h-[100dvh] max-w-[88rem] px-3 pt-3 md:px-6 md:pb-8 md:pt-6"
            : isMaterial
              ? "app-shell app-shell--material relative z-10 mx-auto min-h-[100dvh] max-w-6xl px-4 pt-3 md:px-8 md:pb-10 md:pt-8"
              : "app-shell relative z-10 mx-auto min-h-[100dvh] max-w-6xl px-4 pt-4 md:px-8 md:pb-10 md:pt-8"
      }
      data-tabbar="visible"
    >
      <nav
        className="app-topnav fixed right-4 top-4 z-[100] hidden gap-1 md:flex"
        aria-label="Основная навигация"
      >
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              prefetch
              className={`app-topnav-link rounded-full px-3.5 py-2 text-sm backdrop-blur-md transition ${
                active
                  ? "is-active bg-[var(--accent-soft)] text-[var(--accent)] shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--accent)_28%,transparent)]"
                  : "bg-[color-mix(in_srgb,var(--bg)_70%,transparent)] text-[var(--muted)] hover:text-[var(--ink)]"
              }`}
            >
              {item.label === "Ещё" ? "Настройки" : item.label}
            </Link>
          );
        })}
      </nav>

      {children}

      <nav className="app-tabbar md:hidden" aria-label="Основная навигация">
        <ul className="app-tabbar-list">
          {NAV.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <li key={item.href} className="flex-1">
                <Link
                  href={item.href}
                  className="app-tab"
                  data-active={active}
                  aria-current={active ? "page" : undefined}
                >
                  <NavIcon name={item.icon} active={active} />
                  <span className="app-tab-label">{item.label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}
