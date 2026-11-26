"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { BrandMark } from "@/components/BrandMark";
import { getToken, setToken } from "@/lib/api";

export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;

    const hash = typeof window !== "undefined" ? window.location.hash.replace(/^#/, "") : "";
    const params = new URLSearchParams(hash);
    const token = params.get("token");
    const err = params.get("error");

    if (err) {
      handled.current = true;
      setError(err);
      return;
    }

    // Already stored (Strict Mode / effect re-run after hash stripped)
    if (!token && getToken()) {
      handled.current = true;
      router.replace("/archive");
      return;
    }

    if (!token) {
      handled.current = true;
      setError("Нет токена в ответе Google");
      return;
    }

    handled.current = true;
    setToken(token);
    window.history.replaceState(null, "", "/auth/callback");
    router.replace("/archive");
  }, [router]);

  return (
    <main className="relative z-10 mx-auto flex min-h-[100dvh] max-w-lg flex-col items-center justify-center gap-3 px-6">
      <BrandMark size={48} className="animate-fade rounded-2xl" priority />
      {error ? (
        <>
          <p className="text-center text-sm text-[var(--danger,#b33)]">{error}</p>
          <a href="/" className="text-sm text-[var(--accent)] underline">
            Назад ко входу
          </a>
        </>
      ) : (
        <p className="text-sm text-[var(--muted)] animate-fade">Входим…</p>
      )}
    </main>
  );
}
