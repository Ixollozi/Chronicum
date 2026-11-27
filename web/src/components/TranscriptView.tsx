"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

type TranscriptViewProps = {
  text: string;
  durationSeconds?: number | null;
  sourceType?: string | null;
};

type Cue = {
  sec: number;
  time: string;
  text: string;
};

type Paragraph = {
  time?: string;
  text: string;
};

type Section = {
  id: string;
  badge?: string;
  title: string;
  preview?: string;
  paragraphs: Paragraph[];
  wordCount: number;
  searchBlob: string;
};

type ParsedKind = "chapters" | "buckets" | "pages" | "headings" | "chunks" | "short-timed" | "short-plain";

type ParsedText = {
  kind: ParsedKind;
  isLong: boolean;
  isTimed: boolean;
  wordCount: number;
  durationMin: number | null;
  metaLines: string[];
  sections: Section[];
  rawLines: string[];
};

const TIMED_LINE_RE = /^\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$/;
const CHAPTER_LINE_RE = /^\s*(?:[-•*▸►]\s*)?(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:[-–—:|]\s*|\s+)(.+)$/;
const PDF_PAGE_RE = /^\s*---\s*(?:Страница|Page)\s+(\d+)(?:\s*\/\s*(\d+))?\s*---\s*$/i;
const MD_HEADING_RE = /^\s*(#{1,3})\s+(.+?)\s*$/;
const BANNER_HEADING_RE = /^\s*={3,}\s*(.+?)\s*={3,}\s*$/;

function parseTimestamp(ts: string): number {
  const parts = ts.split(":").map((p) => Number.parseInt(p, 10));
  if (parts.some((n) => Number.isNaN(n))) return 0;
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return 0;
}

function formatTimestamp(sec: number): string {
  const total = Math.max(0, Math.floor(sec));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (h > 0) return `${h}:${pad(m)}:${pad(s)}`;
  return `${pad(m)}:${pad(s)}`;
}

function countWords(s: string): number {
  const trimmed = s.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).length;
}

function firstSnippet(text: string, max = 96): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  return clean.slice(0, max - 1).trimEnd() + "…";
}

function joinCueText(prev: string, next: string): string {
  const a = prev.trim();
  const b = next.trim();
  if (!a) return b;
  if (!b) return a;
  if (a.endsWith("-") && !a.endsWith(" -") && /^[a-zа-яё]/i.test(b)) {
    return a.slice(0, -1) + b;
  }
  return `${a} ${b}`;
}

function groupCuesIntoParagraphs(cues: Cue[]): Paragraph[] {
  if (!cues.length) return [];
  const paragraphs: Paragraph[] = [];
  let startTime = formatTimestamp(cues[0].sec);
  let buf = "";
  let prevSec = cues[0].sec;

  const flush = () => {
    const clean = buf.trim();
    if (clean) {
      paragraphs.push({ time: startTime, text: clean });
    }
    buf = "";
  };

  for (let i = 0; i < cues.length; i++) {
    const cue = cues[i];
    const gap = cue.sec - prevSec;
    if (buf && gap >= 8) {
      flush();
      startTime = formatTimestamp(cue.sec);
    }
    if (!buf) {
      startTime = formatTimestamp(cue.sec);
    }
    buf = joinCueText(buf, cue.text);
    prevSec = cue.sec;

    const endsSentence = /[.!?…]["»”)\]]*$/.test(buf);
    if ((endsSentence && buf.length >= 260) || buf.length >= 480) {
      flush();
    }
  }
  flush();
  return paragraphs;
}

function extractChapters(lines: string[], maxSec: number): Array<{ sec: number; title: string }> {
  const candidates: Array<{ sec: number; title: string }> = [];
  for (const line of lines) {
    const m = line.match(CHAPTER_LINE_RE);
    if (!m) continue;
    const title = m[2].trim();
    if (!title || /^https?:\/\//i.test(title)) continue;
    const sec = parseTimestamp(m[1]);
    if (maxSec > 0 && sec > maxSec + 60) continue;
    candidates.push({ sec, title });
  }
  if (candidates.length < 2) return [];
  // Keep longest non-decreasing sequence starting from earliest chapter
  const ordered: Array<{ sec: number; title: string }> = [];
  for (const c of candidates) {
    if (!ordered.length || c.sec >= ordered[ordered.length - 1].sec) {
      ordered.push(c);
    }
  }
  return ordered.length >= 2 ? ordered : [];
}

function splitPlainIntoParagraphs(lines: string[]): Paragraph[] {
  const out: Paragraph[] = [];
  let buf: string[] = [];
  const flush = () => {
    const joined = buf.join("\n").trim();
    if (joined) out.push({ text: joined });
    buf = [];
  };
  for (const line of lines) {
    if (!line.trim()) {
      flush();
    } else {
      buf.push(line.trimEnd());
    }
  }
  flush();
  return out;
}

function makeSection(
  id: string,
  title: string,
  paragraphs: Paragraph[],
  badge?: string,
  customPreview?: string
): Section {
  const bodyText = paragraphs.map((p) => p.text).join("\n");
  const wc = countWords(bodyText);
  return {
    id,
    badge,
    title,
    preview: customPreview ?? (paragraphs[0] ? firstSnippet(paragraphs[0].text) : undefined),
    paragraphs,
    wordCount: wc,
    searchBlob: `${badge || ""} ${title}\n${bodyText}`.toLowerCase(),
  };
}

function parseRawText(text: string, durationSeconds?: number | null): ParsedText {
  const normalized = text.replace(/\r\n/g, "\n");
  const rawLines = normalized.split("\n");
  const nonEmptyLines = rawLines.filter((l) => l.trim().length > 0);
  const totalWords = countWords(normalized);

  // Locate timed lines
  let firstTimedIdx = -1;
  let lastTimedIdx = -1;
  const timedIndices: number[] = [];
  for (let i = 0; i < rawLines.length; i++) {
    if (TIMED_LINE_RE.test(rawLines[i])) {
      if (firstTimedIdx === -1) firstTimedIdx = i;
      lastTimedIdx = i;
      timedIndices.push(i);
    }
  }

  const isTimed =
    timedIndices.length >= 12 ||
    (timedIndices.length >= 2 && timedIndices.length >= Math.floor(nonEmptyLines.length * 0.35));

  if (isTimed) {
    const leadingLines = rawLines.slice(0, firstTimedIdx).filter((l) => l.trim().length > 0);
    const trailingLines = rawLines.slice(lastTimedIdx + 1);
    const trailingNonEmpty = trailingLines.filter((l) => l.trim().length > 0);

    const cues: Cue[] = [];
    for (let i = firstTimedIdx; i <= lastTimedIdx; i++) {
      const line = rawLines[i];
      const m = line.match(TIMED_LINE_RE);
      if (m) {
        const cueText = m[2].trim();
        if (cueText) {
          cues.push({ sec: parseTimestamp(m[1]), time: m[1], text: cueText });
        }
      } else if (line.trim() && cues.length > 0) {
        // Continuation line inside timed span
        cues[cues.length - 1].text = joinCueText(cues[cues.length - 1].text, line.trim());
      }
    }

    const maxCueSec = cues.length ? cues[cues.length - 1].sec : 0;
    const effectiveDur =
      typeof durationSeconds === "number" && durationSeconds > 0 ? durationSeconds : maxCueSec;
    const durationMin = effectiveDur > 0 ? Math.max(1, Math.round(effectiveDur / 60)) : null;

    const isLong = effectiveDur > 300 || normalized.length > 2600 || cues.length > 75;

    if (!isLong) {
      const paragraphs = groupCuesIntoParagraphs(cues);
      if (trailingNonEmpty.length > 0) {
        paragraphs.push(...splitPlainIntoParagraphs(trailingLines));
      }
      return {
        kind: "short-timed",
        isLong: false,
        isTimed: true,
        wordCount: totalWords,
        durationMin,
        metaLines: leadingLines,
        sections: [makeSection("all", "Транскрипт", paragraphs)],
        rawLines,
      };
    }

    // Long timed transcript: check explicit chapters first (usually in YouTube description tail)
    const chapters = extractChapters([...trailingLines, ...leadingLines], effectiveDur);
    const sections: Section[] = [];

    if (chapters.length >= 2) {
      // Optional pre-chapter cues if first chapter starts after 15s
      if (cues.length && cues[0].sec + 5 < chapters[0].sec) {
        const preCues = cues.filter((c) => c.sec < chapters[0].sec);
        if (preCues.length) {
          sections.push(
            makeSection(
              "ch-pre",
              "Начало",
              groupCuesIntoParagraphs(preCues),
              formatTimestamp(preCues[0].sec)
            )
          );
        }
      }

      for (let i = 0; i < chapters.length; i++) {
        const ch = chapters[i];
        const nextSec = i + 1 < chapters.length ? chapters[i + 1].sec : Number.POSITIVE_INFINITY;
        const chCues = cues.filter((c) => c.sec >= ch.sec && c.sec < nextSec);
        if (!chCues.length) continue;
        sections.push(
          makeSection(
            `ch-${i}`,
            ch.title,
            groupCuesIntoParagraphs(chCues),
            formatTimestamp(ch.sec)
          )
        );
      }

      if (trailingNonEmpty.length > 0) {
        const descParagraphs = splitPlainIntoParagraphs(trailingLines);
        if (descParagraphs.length) {
          sections.push(
            makeSection("desc", "Описание и таймкоды", descParagraphs, "Инфо")
          );
        }
      }

      if (sections.length >= 2) {
        return {
          kind: "chapters",
          isLong: true,
          isTimed: true,
          wordCount: totalWords,
          durationMin,
          metaLines: leadingLines,
          sections,
          rawLines,
        };
      }
    }

    // Fallback for long timed transcript without explicit chapters: 3–5 min time buckets
    const bucketSec = effectiveDur > 1800 ? 300 : effectiveDur > 900 ? 240 : 180;
    const totalSpan = Math.max(effectiveDur, maxCueSec + 1);
    const bucketCount = Math.max(1, Math.ceil(totalSpan / bucketSec));
    for (let b = 0; b < bucketCount; b++) {
      const start = b * bucketSec;
      const end = Math.min(Math.ceil(totalSpan), (b + 1) * bucketSec);
      const bCues = cues.filter(
        (c) => c.sec >= start && (b === bucketCount - 1 ? true : c.sec < (b + 1) * bucketSec)
      );
      if (!bCues.length) continue;
      const rangeLabel = `${formatTimestamp(start)}–${formatTimestamp(end)}`;
      const paragraphs = groupCuesIntoParagraphs(bCues);
      sections.push(
        makeSection(
          `bucket-${b}`,
          firstSnippet(paragraphs[0]?.text || `Блок ${b + 1}`, 72),
          paragraphs,
          rangeLabel,
          paragraphs[1] ? firstSnippet(paragraphs[1].text) : undefined
        )
      );
    }

    if (trailingNonEmpty.length > 0) {
      const descParagraphs = splitPlainIntoParagraphs(trailingLines);
      if (descParagraphs.length) {
        sections.push(makeSection("desc", "Описание и ссылки", descParagraphs, "Инфо"));
      }
    }

    return {
      kind: "buckets",
      isLong: true,
      isTimed: true,
      wordCount: totalWords,
      durationMin,
      metaLines: leadingLines,
      sections,
      rawLines,
    };
  }

  // Non-timed text (PDF, article, note)
  const isLong = normalized.length > 2500 || nonEmptyLines.length > 40;
  const durationMin =
    typeof durationSeconds === "number" && durationSeconds > 0
      ? Math.max(1, Math.round(durationSeconds / 60))
      : null;

  // 1) PDF page markers: "--- Страница X / Y ---"
  const pageMarkerIndices: Array<{ idx: number; num: string; total?: string }> = [];
  for (let i = 0; i < rawLines.length; i++) {
    const m = rawLines[i].match(PDF_PAGE_RE);
    if (m) pageMarkerIndices.push({ idx: i, num: m[1], total: m[2] });
  }

  if (pageMarkerIndices.length >= 2 && isLong) {
    const metaLines = rawLines
      .slice(0, pageMarkerIndices[0].idx)
      .map((l) => l.trim())
      .filter(Boolean);
    const sections: Section[] = [];

    for (let i = 0; i < pageMarkerIndices.length; i++) {
      const cur = pageMarkerIndices[i];
      const nextIdx = i + 1 < pageMarkerIndices.length ? pageMarkerIndices[i + 1].idx : rawLines.length;
      const pageLines = rawLines.slice(cur.idx + 1, nextIdx);
      const paragraphs = splitPlainIntoParagraphs(pageLines);
      if (!paragraphs.length) continue;

      const firstMeaningful =
        pageLines.find((l) => l.trim().length >= 3 && !/^[-•*]+$/.test(l.trim()))?.trim() || "";
      const badge = cur.total ? `Стр. ${cur.num}/${cur.total}` : `Стр. ${cur.num}`;
      const title = firstMeaningful ? firstSnippet(firstMeaningful, 74) : `Страница ${cur.num}`;
      const preview =
        paragraphs.length > 1
          ? firstSnippet(paragraphs[1].text)
          : paragraphs[0].text !== firstMeaningful
            ? firstSnippet(paragraphs[0].text)
            : undefined;
      sections.push(makeSection(`page-${cur.num}-${i}`, title, paragraphs, badge, preview));
    }

    if (sections.length >= 2) {
      return {
        kind: "pages",
        isLong: true,
        isTimed: false,
        wordCount: totalWords,
        durationMin,
        metaLines,
        sections,
        rawLines,
      };
    }
  }

  // 2) Markdown / banner headings
  if (isLong) {
    const headingMarks: Array<{ idx: number; title: string }> = [];
    for (let i = 0; i < rawLines.length; i++) {
      const hm = rawLines[i].match(MD_HEADING_RE);
      if (hm) {
        headingMarks.push({ idx: i, title: hm[2].trim() });
        continue;
      }
      const bm = rawLines[i].match(BANNER_HEADING_RE);
      if (bm) {
        headingMarks.push({ idx: i, title: bm[1].trim() });
      }
    }

    if (headingMarks.length >= 2) {
      const leading = rawLines.slice(0, headingMarks[0].idx);
      const leadingParagraphs = splitPlainIntoParagraphs(leading);
      const metaLines: string[] = [];
      const sections: Section[] = [];

      if (leadingParagraphs.length && leading.join("\n").length <= 450) {
        metaLines.push(...leading.map((l) => l.trim()).filter(Boolean));
      } else if (leadingParagraphs.length) {
        sections.push(makeSection("intro", "Вступление", leadingParagraphs, "0"));
      }

      for (let i = 0; i < headingMarks.length; i++) {
        const h = headingMarks[i];
        const nextIdx = i + 1 < headingMarks.length ? headingMarks[i + 1].idx : rawLines.length;
        const bodyLines = rawLines.slice(h.idx + 1, nextIdx);
        const paragraphs = splitPlainIntoParagraphs(bodyLines);
        if (!paragraphs.length) continue;
        sections.push(makeSection(`sec-${i}`, h.title, paragraphs, `${i + 1}`));
      }

      if (sections.length >= 2) {
        return {
          kind: "headings",
          isLong: true,
          isTimed: false,
          wordCount: totalWords,
          durationMin,
          metaLines,
          sections,
          rawLines,
        };
      }
    }

    // 3) Plain long text fallback -> balanced paragraph chunks
    const allParagraphs = splitPlainIntoParagraphs(rawLines);
    const sections: Section[] = [];
    let curGroup: Paragraph[] = [];
    let curChars = 0;

    const flushChunk = () => {
      if (!curGroup.length) return;
      const idx = sections.length + 1;
      sections.push(
        makeSection(
          `chunk-${idx}`,
          firstSnippet(curGroup[0].text, 74),
          curGroup,
          `Часть ${idx}`,
          curGroup[1] ? firstSnippet(curGroup[1].text) : undefined
        )
      );
      curGroup = [];
      curChars = 0;
    };

    for (const p of allParagraphs) {
      if (curGroup.length > 0 && curChars + p.text.length > 2000) {
        flushChunk();
      }
      curGroup.push(p);
      curChars += p.text.length;
    }
    flushChunk();

    if (sections.length >= 2) {
      return {
        kind: "chunks",
        isLong: true,
        isTimed: false,
        wordCount: totalWords,
        durationMin,
        metaLines: [],
        sections,
        rawLines,
      };
    }
  }

  return {
    kind: "short-plain",
    isLong: false,
    isTimed: false,
    wordCount: totalWords,
    durationMin,
    metaLines: [],
    sections: [makeSection("plain", "Текст", splitPlainIntoParagraphs(rawLines))],
    rawLines,
  };
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function countOccurrences(haystack: string, needleLower: string): number {
  if (!needleLower) return 0;
  let count = 0;
  let pos = 0;
  while (true) {
    const idx = haystack.indexOf(needleLower, pos);
    if (idx === -1) break;
    count++;
    pos = idx + needleLower.length;
  }
  return count;
}

function highlightText(content: string, query: string): ReactNode {
  const q = query.trim();
  if (!q) return content;
  const re = new RegExp(`(${escapeRegExp(q)})`, "gi");
  const parts = content.split(re);
  if (parts.length === 1) return content;
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="tv-mark">
        {part}
      </mark>
    ) : (
      part
    )
  );
}

function pluralRu(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(n) % 100;
  const mod = abs % 10;
  if (abs >= 11 && abs <= 19) return many;
  if (mod === 1) return one;
  if (mod >= 2 && mod <= 4) return few;
  return many;
}

async function copyTextToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fallback below for HTTP LAN origins
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function RawLinesBlock({ lines, isTimed, query }: { lines: string[]; isTimed: boolean; query: string }) {
  if (!isTimed) {
    const full = lines.join("\n");
    return <pre className="deck-raw tv-raw-box">{query ? highlightText(full, query) : full}</pre>;
  }
  return (
    <div className="tv-raw-timed" role="list">
      {lines.map((line, i) => {
        const m = line.match(TIMED_LINE_RE);
        if (!m) {
          if (!line.trim()) return <div key={i} className="transcript-gap" />;
          return (
            <p key={i} className="transcript-line transcript-line--plain">
              {query ? highlightText(line, query) : line}
            </p>
          );
        }
        return (
          <div key={i} className="transcript-line" role="listitem">
            <span className="transcript-time">{m[1]}</span>
            <span className="transcript-text">{query ? highlightText(m[2], query) : m[2]}</span>
          </div>
        );
      })}
    </div>
  );
}

export function TranscriptView({ text, durationSeconds }: TranscriptViewProps) {
  const parsed = useMemo(() => parseRawText(text, durationSeconds), [text, durationSeconds]);
  const [query, setQuery] = useState("");
  const [rawMode, setRawMode] = useState(false);
  const [copied, setCopied] = useState(false);
  const [openIds, setOpenIds] = useState<Set<string>>(() => {
    const first = parsed.sections[0]?.id;
    return first ? new Set([first]) : new Set();
  });

  useEffect(() => {
    const first = parsed.sections[0]?.id;
    setOpenIds(first ? new Set([first]) : new Set());
    setQuery("");
    setRawMode(false);
  }, [text, parsed.sections]);

  const normalizedQuery = query.trim().toLowerCase();

  const filteredSections = useMemo(() => {
    if (!normalizedQuery) {
      return parsed.sections.map((section) => ({ section, matches: 0 }));
    }
    return parsed.sections
      .map((section) => ({
        section,
        matches: countOccurrences(section.searchBlob, normalizedQuery),
      }))
      .filter((entry) => entry.matches > 0);
  }, [parsed.sections, normalizedQuery]);

  const totalMatches = useMemo(() => {
    if (!normalizedQuery) return 0;
    if (rawMode) return countOccurrences(text.toLowerCase(), normalizedQuery);
    return filteredSections.reduce((sum, e) => sum + e.matches, 0);
  }, [filteredSections, normalizedQuery, rawMode, text]);

  const allExpanded =
    parsed.sections.length > 0 && parsed.sections.every((s) => openIds.has(s.id));

  const toggleSection = useCallback((id: string) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    if (allExpanded) {
      setOpenIds(new Set());
    } else {
      setOpenIds(new Set(parsed.sections.map((s) => s.id)));
    }
  }, [allExpanded, parsed.sections]);

  const handleCopy = useCallback(async () => {
    const ok = await copyTextToClipboard(text);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    }
  }, [text]);

  // Short materials: stay simple, clean, immediately readable
  if (!parsed.isLong) {
    const statsParts: string[] = [
      `${parsed.wordCount} ${pluralRu(parsed.wordCount, "слово", "слова", "слов")}`,
    ];
    if (parsed.durationMin) statsParts.push(`~${parsed.durationMin} мин`);

    return (
      <div className="transcript-view tv-root">
        <div className="tv-short-bar">
          <span className="tv-stats">{statsParts.join(" · ")}</span>
          <div className="tv-actions">
            {parsed.isTimed && (
              <button
                type="button"
                className="tv-btn"
                data-active={rawMode}
                onClick={() => setRawMode((v) => !v)}
              >
                {rawMode ? "Абзацами" : "Построчно"}
              </button>
            )}
            <button type="button" className="tv-btn" onClick={() => void handleCopy()}>
              {copied ? "Скопировано" : "Копировать"}
            </button>
          </div>
        </div>

        {parsed.metaLines.length > 0 && !rawMode && (
          <div className="tv-meta">
            {parsed.metaLines.map((line, i) => (
              <p key={i}>{line}</p>
            ))}
          </div>
        )}

        {rawMode ? (
          <RawLinesBlock lines={parsed.rawLines} isTimed={parsed.isTimed} query="" />
        ) : parsed.isTimed ? (
          <div className="tv-paragraphs" role="list">
            {parsed.sections[0]?.paragraphs.map((p, i) => (
              <div
                key={i}
                className={p.time ? "tv-para" : "tv-para tv-para--plain"}
                role="listitem"
              >
                {p.time && <span className="transcript-time">{p.time}</span>}
                <p className="tv-para-text">{p.text}</p>
              </div>
            ))}
          </div>
        ) : (
          <pre className="deck-raw">{text}</pre>
        )}
      </div>
    );
  }

  // Long materials: compact toolbar + search + collapsible accordion sections
  const sectionNoun =
    parsed.kind === "chapters"
      ? pluralRu(parsed.sections.length, "глава", "главы", "глав")
      : parsed.kind === "pages"
        ? pluralRu(parsed.sections.length, "страница", "страницы", "страниц")
        : parsed.kind === "buckets"
          ? pluralRu(parsed.sections.length, "блок", "блока", "блоков")
          : pluralRu(parsed.sections.length, "раздел", "раздела", "разделов");

  const statsParts: string[] = [
    `${parsed.wordCount.toLocaleString("ru-RU")} ${pluralRu(parsed.wordCount, "слово", "слова", "слов")}`,
  ];
  if (parsed.durationMin) statsParts.push(`${parsed.durationMin} мин`);
  statsParts.push(`${parsed.sections.length} ${sectionNoun}`);

  return (
    <div className="transcript-view tv-root">
      <div className="tv-toolbar">
        <div className="tv-toolbar-top">
          <div className="tv-search-wrap">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Поиск по тексту…"
              aria-label="Поиск по полному тексту"
              className="tv-search"
            />
            {query && (
              <button
                type="button"
                className="tv-search-clear"
                aria-label="Очистить поиск"
                onClick={() => setQuery("")}
              >
                ×
              </button>
            )}
          </div>
          <div className="tv-actions">
            {!rawMode && !normalizedQuery && (
              <button type="button" className="tv-btn" onClick={toggleAll}>
                {allExpanded ? "Свернуть всё" : "Развернуть всё"}
              </button>
            )}
            <button
              type="button"
              className="tv-btn"
              data-active={rawMode}
              onClick={() => setRawMode((v) => !v)}
              title="Переключить между компактными блоками и исходным сырым текстом"
            >
              {rawMode ? "Блоками" : "Исходник"}
            </button>
            <button type="button" className="tv-btn" onClick={() => void handleCopy()}>
              {copied ? "Скопировано" : "Копировать"}
            </button>
          </div>
        </div>

        <div className="tv-toolbar-meta">
          <span className="tv-stats">{statsParts.join(" · ")}</span>
          {normalizedQuery && (
            <span className="tv-match-summary">
              {totalMatches > 0
                ? rawMode
                  ? `${totalMatches} ${pluralRu(totalMatches, "совпадение", "совпадения", "совпадений")}`
                  : `${totalMatches} ${pluralRu(totalMatches, "совпадение", "совпадения", "совпадений")} в ${filteredSections.length} ${pluralRu(filteredSections.length, "блоке", "блоках", "блоках")}`
                : "Совпадений нет"}
            </span>
          )}
        </div>
      </div>

      {parsed.metaLines.length > 0 && !rawMode && (
        <div className="tv-meta">
          {parsed.metaLines.map((line, i) => (
            <p key={i}>{normalizedQuery ? highlightText(line, query) : line}</p>
          ))}
        </div>
      )}

      {rawMode ? (
        <RawLinesBlock lines={parsed.rawLines} isTimed={parsed.isTimed} query={query} />
      ) : filteredSections.length === 0 ? (
        <div className="tv-empty">
          По запросу «{query.trim()}» ничего не найдено.
        </div>
      ) : (
        <div className="tv-accordion" role="list">
          {filteredSections.map(({ section, matches }) => {
            const isOpen = Boolean(normalizedQuery) || openIds.has(section.id);
            return (
              <section
                key={section.id}
                className="tv-section"
                data-open={isOpen ? "true" : "false"}
                role="listitem"
              >
                <button
                  type="button"
                  className="tv-section-head"
                  aria-expanded={isOpen}
                  onClick={() => toggleSection(section.id)}
                >
                  <span className="tv-section-lead">
                    {section.badge && <span className="tv-badge">{section.badge}</span>}
                    <span className="tv-section-titles">
                      <span className="tv-section-title">
                        {normalizedQuery ? highlightText(section.title, query) : section.title}
                      </span>
                      {!isOpen && section.preview && (
                        <span className="tv-section-preview">{section.preview}</span>
                      )}
                    </span>
                  </span>
                  <span className="tv-section-right">
                    {normalizedQuery && matches > 0 ? (
                      <span className="tv-match-pill">{matches}</span>
                    ) : (
                      <span className="tv-wc">{section.wordCount} сл.</span>
                    )}
                    <span className="tv-chevron" aria-hidden="true">
                      ▾
                    </span>
                  </span>
                </button>

                {isOpen && (
                  <div className="tv-section-body">
                    <div className="tv-paragraphs">
                      {section.paragraphs.map((p, idx) => (
                        <div
                          key={idx}
                          className={p.time ? "tv-para" : "tv-para tv-para--plain"}
                        >
                          {p.time && <span className="transcript-time">{p.time}</span>}
                          <p className="tv-para-text">
                            {normalizedQuery ? highlightText(p.text, query) : p.text}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
