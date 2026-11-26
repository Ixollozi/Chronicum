"use client";

import { useEffect } from "react";

export function PwaRegister() {
  useEffect(() => {
    if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Service worker registration is best-effort (e.g. disabled in private browsing or HTTP)
    });
  }, []);

  return null;
}
