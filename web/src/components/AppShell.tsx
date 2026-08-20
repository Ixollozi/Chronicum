"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { BrandMark } from "@/components/BrandMark";
import { getToken, setToken } from "@/lib/api";

const NAV = [
  { href: "/", label: "Главная", icon: "home" as const },
  { href: "/graph", label: "Сеть", icon: "graph" as const },
  { href: "/archive", label: "Архив", icon: "archive" as const },
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
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const t = getToken();
    setAuthed(Boolean(t));
    setReady(true);
  }, []);

  if (!ready) {
    return (
      <main className="relative z-10 mx-auto flex min-h-[100dvh] max-w-lg flex-col items-center justify-center gap-3 px-6">
        <BrandMark size={48} className="animate-fade rounded-2xl" priority />
        <p className="text-sm text-[var(--muted)] animate-fade">Chronicum</p>
      </main>
    );
  }

  if (!authed) {
    return (
      <main className="relative z-10 mx-auto flex min-h-[100dvh] max-w-lg flex-col justify-center px-6 py-16">
        <BrandMark size={56} className="mb-6 animate-fade rounded-2xl" priority />
        <p className="mb-3 text-[11px] uppercase tracking-[0.28em] text-[var(--muted)] animate-fade">
          Personal knowledge
        </p>
        <h1
          className="animate-rise text-[clamp(3rem,12vw,4.5rem)] leading-[0.92] tracking-[-0.03em]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          Chronicum
        </h1>
        <p className="mt-4 max-w-md text-[var(--muted)] animate-rise" style={{ animationDelay: "80ms" }}>
          Одна строка — спросить, сохранить ссылку или заметку. Сеть — увидеть связи.
        </p>
        <form
          className="mt-10 space-y-4 animate-rise"
          style={{ animationDelay: "140ms" }}
          onSubmit={(e) => {
            e.preventDefault();
            setToken(tokenInput.trim() || "chronicum-dev-token");
            setAuthed(true);
          }}
        >
          <label className="block text-sm text-[var(--muted)]">
            WEB_API_TOKEN
            <input
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="chronicum-dev-token"
              autoComplete="current-password"
              className="mt-2 w-full border-b border-[var(--line)] bg-transparent px-0 py-3 text-base outline-none transition focus:border-[var(--accent)]"
            />
          </label>
          <button
            type="submit"
            className="w-full rounded-[var(--radius-pill)] border border-[var(--accent)] bg-[var(--accent)] px-5 py-3 text-sm text-[var(--btn-fg)] transition hover:brightness-110"
          >
            Войти
          </button>
        </form>
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
      {/* Desktop nav — same fixed spot on every page (as on «Сеть») */}
      <nav className="pointer-events-none fixed right-4 top-4 z-40 hidden gap-1 md:flex">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`pointer-events-auto rounded-full px-3.5 py-2 text-sm backdrop-blur-md transition ${
                active
                  ? "bg-[var(--accent-soft)] text-[var(--accent)] shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--accent)_28%,transparent)]"
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
