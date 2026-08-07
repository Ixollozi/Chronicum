"use client";

/** Timed transcript lines like "[0:00] text" — keep out of Markdown. */
export function TranscriptView({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const timed = lines.filter((l) => /^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]/.test(l));
  const useTimed = timed.length >= Math.max(2, Math.floor(lines.filter(Boolean).length * 0.4));

  if (!useTimed) {
    return <pre className="deck-raw">{text}</pre>;
  }

  return (
    <div className="transcript-view" role="list">
      {lines.map((line, i) => {
        const m = line.match(/^\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$/);
        if (!m) {
          if (!line.trim()) return <div key={i} className="transcript-gap" />;
          return (
            <p key={i} className="transcript-line transcript-line--plain">
              {line}
            </p>
          );
        }
        return (
          <div key={i} className="transcript-line" role="listitem">
            <span className="transcript-time">{m[1]}</span>
            <span className="transcript-text">{m[2]}</span>
          </div>
        );
      })}
    </div>
  );
}
