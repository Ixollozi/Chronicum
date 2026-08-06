"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getToken, setToken } from "@/lib/api";

const NAV = [
  { href: "/", label: "Главная", hint: "единая строка" },
  { href: "/graph", label: "Сеть", hint: "граф знаний" },
  { href: "/archive", label: "Архив", hint: "список" },
  { href: "/tags", label: "Теги", hint: "словарь" },
  { href: "/settings", label: "Настр.", hint: "режимы и ключи" },
];

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

  if (!ready) return null;

  if (!authed) {
    return (
      <main className="relative z-10 mx-auto flex min-h-[100dvh] max-w-lg flex-col justify-center px-6 py-16">
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
              className="mt-2 w-full border-b border-[var(--line)] bg-transparent px-0 py-3 outline-none transition focus:border-[var(--accent)]"
            />
          </label>
          <button
            type="submit"
            className="w-full border border-[var(--accent)] bg-[var(--accent)] px-5 py-3 text-sm text-[var(--btn-fg)] transition hover:brightness-110"
          >
            Войти
          </button>
        </form>
      </main>
    );
  }

  const hideChrome = pathname.startsWith("/m/");
  const isGraph = pathname === "/graph";

  return (
    <div
      className={
        isGraph
          ? "relative z-10 min-h-[100dvh] pb-20 md:pb-0"
          : "relative z-10 mx-auto min-h-[100dvh] max-w-6xl px-4 pb-28 pt-5 md:px-8 md:pb-10 md:pt-8"
      }
    >
      {!hideChrome && !isGraph && (
        <header className="mb-6 flex items-end justify-between gap-4 md:mb-8">
          <Link href="/" className="animate-rise">
            <p className="mb-1 text-[10px] uppercase tracking-[0.28em] text-[var(--muted)]">Chronicum</p>
            <span
              className="block text-[clamp(1.6rem,4vw,2.1rem)] leading-none tracking-[-0.03em]"
              style={{ fontFamily: "var(--font-display)" }}
            >
              {pathname === "/archive"
                ? "Архив"
                : pathname === "/tags"
                  ? "Теги"
                  : pathname === "/settings"
                    ? "Настройки"
                    : "Главная"}
            </span>
          </Link>
          <nav className="hidden items-center gap-1 md:flex animate-fade">
            {NAV.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`px-3 py-2 text-sm transition ${
                    active
                      ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                      : "text-[var(--muted)] hover:text-[var(--ink)]"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </header>
      )}

      {children}

      {!hideChrome && (
        <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-[var(--line)] bg-[color-mix(in_srgb,var(--bg-deep)_92%,transparent)] px-2 pb-[max(0.6rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur-md md:hidden">
          <ul className="mx-auto flex max-w-lg items-stretch justify-between gap-1">
            {NAV.map((item) => {
              const active = pathname === item.href;
              return (
                <li key={item.href} className="flex-1">
                  <Link
                    href={item.href}
                    className={`flex flex-col items-center rounded-lg px-1 py-2 text-center transition ${
                      active ? "text-[var(--accent)]" : "text-[var(--muted)]"
                    }`}
                  >
                    <span className="text-[13px] font-medium leading-none">{item.label}</span>
                    <span className="mt-1 text-[10px] opacity-70">{item.hint}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      )}

      {isGraph && (
        <nav className="pointer-events-none fixed right-4 top-4 z-40 hidden gap-1 md:flex">
          {NAV.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`pointer-events-auto rounded-lg px-3 py-2 text-sm backdrop-blur-md transition ${
                  active
                    ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                    : "bg-[color-mix(in_srgb,var(--bg)_70%,transparent)] text-[var(--muted)] hover:text-[var(--ink)]"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      )}
    </div>
  );
}
