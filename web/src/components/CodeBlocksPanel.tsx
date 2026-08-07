"use client";

import { useState } from "react";
import { RichText } from "@/components/RichText";
import type { CodeBlock } from "@/lib/api";

function fence(language: string, code: string): string {
  const lang = (language || "text").replace(/[^a-z0-9_+-]/gi, "") || "text";
  return `\`\`\`${lang}\n${code}\n\`\`\``;
}

export function CodeBlocksPanel({ blocks }: { blocks: CodeBlock[] }) {
  const [copied, setCopied] = useState<number | null>(null);

  async function copyBlock(i: number, code: string) {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(i);
      window.setTimeout(() => setCopied((cur) => (cur === i ? null : cur)), 1200);
    } catch {
      /* ignore */
    }
  }

  if (!blocks.length) return null;

  return (
    <div className="deck-code-list">
      {blocks.map((block, i) => {
        const title = block.title || `Фрагмент ${i + 1}`;
        const lang = block.language || "text";
        return (
          <article key={`${title}-${i}`} className="deck-code-block" style={{ animationDelay: `${i * 40}ms` }}>
            <header className="deck-code-head">
              <div className="deck-code-meta">
                <h3 className="deck-code-title">{title}</h3>
                <span className="deck-code-lang">{lang}</span>
              </div>
              <button
                type="button"
                className="deck-code-copy"
                onClick={() => void copyBlock(i, block.code)}
              >
                {copied === i ? "Скопировано" : "Копировать"}
              </button>
            </header>
            <RichText text={fence(lang, block.code)} className="deck-code-body" />
          </article>
        );
      })}
    </div>
  );
}
