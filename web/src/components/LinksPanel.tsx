"use client";

import type { MaterialLink } from "@/lib/api";

const SOURCE_LABEL: Record<string, string> = {
  speech: "Речь",
  ocr: "Экран",
  llm: "ИИ",
  brand: "Бренд",
};

function hostLabel(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "") + (u.pathname !== "/" ? u.pathname : "");
  } catch {
    return url;
  }
}

export function LinksPanel({ links }: { links: MaterialLink[] }) {
  if (!links.length) return null;

  return (
    <ul className="deck-links">
      {links.map((link, i) => {
        const title = (link.title || "").trim() || hostLabel(link.url);
        const source = (link.source || "").toLowerCase();
        const chip = SOURCE_LABEL[source] || (source ? source : "");
        return (
          <li key={`${link.url}-${i}`} style={{ animationDelay: `${i * 40}ms` }}>
            <a href={link.url} target="_blank" rel="noreferrer" className="deck-link-card">
              <span className="deck-link-title">{title}</span>
              <span className="deck-link-url">{link.url}</span>
            </a>
            {chip ? <span className="deck-link-source">{chip}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}
