"use client";

import { useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8010";
const TTS_VOICE_KEY = "chronicum_tts_voice";

type TtsVoice = "svetlana" | "dmitry";

function fmtTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

export function stripForSpeech(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*]\([^)]*\)/g, " ")
    .replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)]/g, " ")
    .replace(/\[([^\]]+)]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^>\s?/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/^\d+\.\s+/gm, "")
    .replace(/[*_~|#]+/g, " ")
    .replace(/\n{2,}/g, ". ")
    .replace(/\n+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Short clips = fast first sound; rest prefetched while playing. */
function splitSpeakChunks(text: string): string[] {
  const clean = stripForSpeech(text);
  if (!clean) return [];
  const sentences = clean
    .split(/(?<=[.!?…»])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (!sentences.length) return [clean.slice(0, 240)];

  const chunks: string[] = [];
  let i = 0;
  if (
    sentences[0].length <= 160 &&
    sentences[1] &&
    sentences[0].length + sentences[1].length <= 220
  ) {
    chunks.push(`${sentences[0]} ${sentences[1]}`);
    i = 2;
  } else {
    chunks.push(sentences[0]);
    i = 1;
  }

  let buf = "";
  const max = 260;
  for (; i < sentences.length; i++) {
    const s = sentences[i];
    if ((buf + " " + s).trim().length > max && buf) {
      chunks.push(buf.trim());
      buf = s;
    } else {
      buf = buf ? `${buf} ${s}` : s;
    }
  }
  if (buf.trim()) chunks.push(buf.trim());
  return chunks;
}

function loadVoice(): TtsVoice {
  if (typeof window === "undefined") return "svetlana";
  const v = localStorage.getItem(TTS_VOICE_KEY);
  return v === "dmitry" ? "dmitry" : "svetlana";
}

const VOICE_LABEL: Record<TtsVoice, string> = {
  svetlana: "Светлана",
  dmitry: "Дмитрий",
};

async function fetchTtsBlob(text: string, voice: TtsVoice, signal?: AbortSignal): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/tts`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${getToken()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text, voice }),
    signal,
  });
  if (!res.ok) {
    let detail = "Не удалось озвучить";
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.blob();
}

function playBlob(el: HTMLAudioElement, blob: Blob): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const cleanup = () => {
      URL.revokeObjectURL(url);
      el.onended = null;
      el.onerror = null;
    };
    el.onended = () => {
      cleanup();
      resolve();
    };
    el.onerror = () => {
      cleanup();
      reject(new Error("playback failed"));
    };
    el.src = url;
    el.playbackRate = 1;
    void el.play().catch((err) => {
      cleanup();
      reject(err);
    });
  });
}

export function MaterialListen({
  materialId,
  hasAudio,
  durationSeconds,
  speakText,
  speakLabel,
  speakKey,
}: {
  materialId: string;
  hasAudio: boolean;
  durationSeconds?: number | null;
  speakText: string;
  speakLabel?: string;
  speakKey: string;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const ttsRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runIdRef = useRef(0);
  const prefetchRef = useRef<Map<string, Promise<Blob>>>(new Map());
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [audioErr, setAudioErr] = useState<string | null>(null);
  const [loadingAudio, setLoadingAudio] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(durationSeconds || 0);
  const [speaking, setSpeaking] = useState(false);
  const [ttsBusy, setTtsBusy] = useState(false);
  const [ttsErr, setTtsErr] = useState<string | null>(null);
  const [voice, setVoice] = useState<TtsVoice>("svetlana");
  const speakTextRef = useRef(speakText);
  speakTextRef.current = speakText;
  const voiceRef = useRef(voice);
  voiceRef.current = voice;

  useEffect(() => {
    setVoice(loadVoice());
  }, []);

  useEffect(() => {
    if (!hasAudio) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    setLoadingAudio(true);
    setAudioErr(null);

    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/materials/${materialId}/audio`, {
          headers: { Authorization: `Bearer ${getToken()}` },
        });
        if (!res.ok) throw new Error("Аудио недоступно");
        const blob = await res.blob();
        objectUrl = URL.createObjectURL(blob);
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setBlobUrl(objectUrl);
      } catch (e) {
        if (!cancelled) setAudioErr(e instanceof Error ? e.message : "Ошибка аудио");
      } finally {
        if (!cancelled) setLoadingAudio(false);
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      audioRef.current?.pause();
    };
  }, [materialId, hasAudio]);

  function cacheKey(chunk: string, v: TtsVoice) {
    return `${v}::${chunk}`;
  }

  function prefetchChunk(chunk: string, v: TtsVoice, signal?: AbortSignal) {
    const key = cacheKey(chunk, v);
    const existing = prefetchRef.current.get(key);
    if (existing) return existing;
    const p = fetchTtsBlob(chunk, v, signal).catch((err) => {
      prefetchRef.current.delete(key);
      throw err;
    });
    prefetchRef.current.set(key, p);
    return p;
  }

  // Warm first clip as soon as the tab text is ready — click often hits cache
  useEffect(() => {
    const text = stripForSpeech(speakText);
    if (!text) return;
    const chunks = splitSpeakChunks(text);
    if (!chunks[0]) return;
    const ac = new AbortController();
    void prefetchChunk(chunks[0], voiceRef.current, ac.signal).catch(() => undefined);
    if (chunks[1]) {
      window.setTimeout(() => {
        void prefetchChunk(chunks[1], voiceRef.current, ac.signal).catch(() => undefined);
      }, 120);
    }
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [speakKey, voice, speakText]);

  function stopSpeak() {
    runIdRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    const el = ttsRef.current;
    if (el) {
      el.pause();
      el.removeAttribute("src");
      el.load();
    }
    setSpeaking(false);
    setTtsBusy(false);
  }

  useEffect(() => {
    return () => stopSpeak();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    stopSpeak();
    setTtsErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [speakKey]);

  function togglePlay() {
    const el = audioRef.current;
    if (!el || !blobUrl) return;
    if (el.paused) {
      stopSpeak();
      void el.play();
    } else {
      el.pause();
    }
  }

  function setTtsVoice(next: TtsVoice) {
    setVoice(next);
    localStorage.setItem(TTS_VOICE_KEY, next);
    if (speaking || ttsBusy) stopSpeak();
  }

  async function toggleSpeak() {
    if (speaking || ttsBusy) {
      stopSpeak();
      return;
    }
    const text = stripForSpeech(speakTextRef.current);
    if (!text) return;
    const chunks = splitSpeakChunks(text);
    if (!chunks.length) return;

    audioRef.current?.pause();
    const runId = ++runIdRef.current;
    const ac = new AbortController();
    abortRef.current = ac;
    setTtsBusy(true);
    setTtsErr(null);

    let el = ttsRef.current;
    if (!el) {
      el = new Audio();
      ttsRef.current = el;
    }

    try {
      // Kick off first clip (often already prefetched)
      let nextBlob = prefetchChunk(chunks[0], voice, ac.signal);
      for (let i = 0; i < chunks.length; i++) {
        if (runId !== runIdRef.current) return;
        // Prefetch look-ahead while we wait / play
        if (chunks[i + 1]) void prefetchChunk(chunks[i + 1], voice, ac.signal);
        if (chunks[i + 2]) void prefetchChunk(chunks[i + 2], voice, ac.signal);

        const blob = await nextBlob;
        if (runId !== runIdRef.current) return;
        setTtsBusy(false);
        setSpeaking(true);

        if (chunks[i + 1]) {
          nextBlob = prefetchChunk(chunks[i + 1], voice, ac.signal);
        }

        await playBlob(el, blob);
      }
      if (runId === runIdRef.current) setSpeaking(false);
    } catch (e) {
      if (ac.signal.aborted || runId !== runIdRef.current) return;
      setTtsErr(e instanceof Error ? e.message : "Ошибка озвучки");
      setSpeaking(false);
    } finally {
      if (runId === runIdRef.current) {
        setTtsBusy(false);
        abortRef.current = null;
      }
    }
  }

  const canSpeak = stripForSpeech(speakText).length > 0;
  const showOriginal = hasAudio;

  if (!showOriginal && !canSpeak) return null;

  return (
    <div className="material-listen">
      {showOriginal && (
        <div className="material-listen-row">
          {blobUrl && (
            <audio
              ref={audioRef}
              src={blobUrl}
              preload="metadata"
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
              onLoadedMetadata={(e) => {
                const d = e.currentTarget.duration;
                if (Number.isFinite(d) && d > 0) setDuration(d);
              }}
            />
          )}
          <button
            type="button"
            className="material-listen-btn"
            disabled={!blobUrl || loadingAudio}
            onClick={togglePlay}
            aria-label={playing ? "Пауза" : "Слушать оригинал"}
          >
            {loadingAudio ? "…" : playing ? "❚❚" : "▶"}
          </button>
          <div className="material-listen-main">
            <div className="material-listen-label">Оригинал</div>
            <div className="material-listen-meta">
              {audioErr
                ? audioErr
                : loadingAudio
                  ? "Загрузка…"
                  : `${fmtTime(current)} / ${fmtTime(duration || durationSeconds || 0)}`}
            </div>
            {blobUrl && (
              <input
                type="range"
                min={0}
                max={Math.max(1, duration || 1)}
                step={0.1}
                value={Math.min(current, duration || current)}
                className="material-listen-seek"
                aria-label="Позиция воспроизведения"
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setCurrent(v);
                  if (audioRef.current) audioRef.current.currentTime = v;
                }}
              />
            )}
          </div>
        </div>
      )}

      {canSpeak && (
        <div className="material-listen-row material-listen-row--tts">
          <button
            type="button"
            className="material-listen-btn material-listen-btn--tts"
            data-active={speaking || ttsBusy}
            onClick={() => void toggleSpeak()}
            aria-label={speaking || ttsBusy ? "Остановить озвучку" : "Озвучить раздел"}
          >
            {ttsBusy ? "…" : speaking ? "■" : "♫"}
          </button>
          <div className="material-listen-main">
            <div className="material-listen-label">
              Озвучка · {speakLabel || "раздел"}
            </div>
            <div className="material-listen-meta">
              {ttsErr
                ? ttsErr
                : ttsBusy
                  ? "Стартую…"
                  : speaking
                    ? `Читает ${VOICE_LABEL[voice]}…`
                    : "Нейро-чтение · старт почти сразу"}
            </div>
            <div className="material-listen-voices" role="group" aria-label="Голос озвучки">
              <button
                type="button"
                className="material-listen-voice"
                data-active={voice === "svetlana"}
                onClick={() => setTtsVoice("svetlana")}
              >
                Светлана
              </button>
              <button
                type="button"
                className="material-listen-voice"
                data-active={voice === "dmitry"}
                onClick={() => setTtsVoice("dmitry")}
              >
                Дмитрий
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
