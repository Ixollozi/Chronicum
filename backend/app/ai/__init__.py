from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.ai.schemas import KnowledgeCard, LLMResult, TranscribeResult, TranscriptSegment
from app.config import Settings, get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

CARD_JSON_SCHEMA_HINT = """
Return ONLY valid JSON (no markdown fences) with this exact shape:
{
  "title": "string",
  "summary": "2-3 sentences: context + hook + CTA if any. Do NOT enumerate every step.",
  "main_idea": "1 thesis why this is worth saving — NOT a copy of the step list",
  "digest": "3-7 actionable bullets as one string with newlines; bullets name concrete tools/sites ONLY when literally present in the material or SCREEN RESOURCES; never add a tool just to fill a bullet; max ~800 chars",
  "key_ideas": ["2-4 abstract principles — MUST NOT paraphrase digest bullets"],
  "practical_takeaways": ["do X via Y — each item names a concrete tool/service"],
  "tools": ["named tools/apps from speech OR screen"],
  "technologies": ["..."],
  "links": [{"url": "https://...", "title": "short Russian label", "source": "llm"}],
  "code_blocks": [{"title": "short Russian label", "language": "python|javascript|bash|css|sql|text", "code": "light runnable snippet"}],
  "category": "EXACT name from Existing categories only, or \"\" if none fit. NEVER invent / force a mismatched category (e.g. do not pick Маркетинг for unrelated banter)",
  "tags": ["2-6 tags; REUSE existing approved tags verbatim when they fit; avoid near-duplicates; skip fluff tags for empty/noise content"],
  "usefulness_signals": {
    "actionability": 1-5,
    "novelty": 1-5,
    "specificity": 1-5,
    "reuse_likelihood": 1-5,
    "personal_fit": 1-5
  },
  "usefulness_score": 1-10,
  "usefulness_reason": "Must mention personal_fit vs profile (what matched / missed) + weakest quality signal"
}

FIELD ROLES (anti-duplication — critical):
- main_idea ≠ summary ≠ digest. Each field has a distinct job above.
- key_ideas = principles (\"B-roll must match speech\"), NOT repeating \"Шаг 1… Шаг 2…\".
- If SCREEN RESOURCES lists brands, put them in tools and mention ≥2 in digest.
- Listicle / \"N tips\" without unique numbers/playbook → actionability ≤ 3.
- End-of-video CTA (Telegram / subscribe / channel promo) is NOT a listicle item — do not put CTA into tools, links, or digest bullets as another \"project\".
- Shallow promo listicle (many names, no procedure) → keep actionability ≤ 3; expect calibrated score around 6–7, never 8+.
- links: ONLY URLs literally present in speech/OCR/caption/screen text (character-for-character as seen/heard). NEVER construct a URL from a brand name, never add «well-known official pages» (no claude.ai / notion.so / reddit.com unless that exact URL is in the material). If a repo/site is only NAMED («ссылку пришлю в комментариях», «выложил на гитхабе»), links stays EMPTY — put the product in tools instead; the system resolves its repo online and verifies it server-side. NEVER guess owner/repo («github.com/someuser/...») yourself. title in Russian. Do NOT put brand names without URLs here — those belong in tools.
- tools: ONLY real software/products the viewer would open, install or run, and that are literally named in speech/OCR/caption. NEVER copy OCR gibberish — Latinized-Russian nonsense tokens («MupyioTcyHaoc», «FalCup», «TenbHblenpewMyuec») are OCR noise, not product names. Content platforms used as placement channels (Reddit, Wikipedia, Хабр, DTF) are NOT tools — describe them in digest instead. Max 8 tools.
- NEGATION: a tool presented as rejected/replaced/crossed-out («не открывая After Effects», «not one frame in After Effects», «вместо X») is an ANTI-example — do NOT put it in tools; you may mention «без X» in digest.
- Do NOT invent secondary workflows absent from speech/OCR (e.g. «use Claude to rewrite scripts») — only tools actually shown or spoken. Every digest bullet must be traceable to a specific speech line / OCR text / caption — no added editors, libraries, sites or steps (no Notion, p5.js, Three.js, CapCut unless literally named).
- Do NOT invent model versions, numbers or CTAs not in the material («Claude Opus 5.5» only if literally said/shown; no generic «подписывайтесь» closers in summary unless the author says it).
- If SCREEN RESOURCES names a scanner/product (SkillSpector, Rybbit, …), that product MUST appear in tools; logo-only OCR brands that never appear in speech should NOT dominate tools/digest.
- code_blocks: ONLY when the material includes or clearly dictates copy-pasteable light code (CSS/JS/Python/bash/SQL snippets, short functions, commands). Keep each block ≤ ~40 lines. language must match content. Empty [] when there is no code. Do NOT invent full apps or tutorials not in the source.

QUALITY RUBRIC (rate the 4 quality signals independently of the person):
3 = typical ok industry tip. usefulness_score is recalibrated server-side from signals + personal_fit.
1–2 quality: noise / empty motivation
3–4: vague tips, little to execute
5–6: DEFAULT for solid material worth saving
7: strong reusable playbook
8+: rare — needs actionability≥4 AND reuse≥4; almost never 9–10

PERSONAL_FIT (1–5) — score for THIS profile only:
1 — anti-focus / irrelevant to role & priorities
2 — weakly related, not useful for current goals
3 — neutral OR profile not set
4 — clearly on-focus / helps a stated goal or priority
5 — directly serves current priorities + focuses (high leverage for them)

Rules:
- First rate quality signals honestly; then personal_fit from focuses / anti_focuses / priorities / horizon.
- If profile is not set → personal_fit MUST be 3.
- On-focus niche material: do NOT punish novelty just because "everyone knows it" if it helps their priorities.
- Off-focus masterpiece: quality may be high, but personal_fit 1–2.
- If horizon is immediate and fit≥4, lean reuse_likelihood higher when they can apply this week.
- Most content land around 5–6 after calibration. Do NOT default quality signals to 4–5.
""".strip()

TASK_JSON_SCHEMA_HINT = """
Return ONLY valid JSON (no markdown fences) with this exact shape — SAME keys as a knowledge card, but field meanings for a TASK:
{
  "title": "short imperative action (глагол + объект)",
  "summary": "что считается ГОТОВО — критерий результата в 1–2 предложениях",
  "main_idea": "следующий конкретный шаг (один)",
  "digest": "чеклист 3–6 строк (каждая — действие)",
  "key_ideas": ["шаги / подзадачи по порядку"],
  "practical_takeaways": ["блокеры, риски, дедлайн/когда сделать — если упомянуто"],
  "tools": ["инструменты если нужны для задачи"],
  "technologies": [],
  "links": [],
  "code_blocks": [],
  "category": "EXACT name from Existing categories only, or \"\"",
  "tags": ["2–5 tags"],
  "usefulness_signals": {
    "actionability": 1-5,
    "novelty": 1-5,
    "specificity": 1-5,
    "reuse_likelihood": 1-5,
    "personal_fit": 1-5
  },
  "usefulness_score": 1-10,
  "usefulness_reason": "почему эта задача важна / срочна относительно профиля"
}

Rules:
- Do NOT invent fake "key principles" — key_ideas are executable steps.
- Empty tools/technologies/links/code_blocks when not needed.
- If input is vague, still produce a concrete next_step in main_idea and askable blockers in practical_takeaways.
- Respond in Russian when the input is Russian.
""".strip()

RULE_JSON_SCHEMA_HINT = """
Return ONLY valid JSON (no markdown fences) with this exact shape — SAME keys as a knowledge card, but field meanings for a RULE:
{
  "title": "короткое имя правила",
  "summary": "когда применять (контекст / триггер)",
  "main_idea": "формулировка правила — одна чёткая норма",
  "digest": "1–3 строки: правило + когда включать",
  "key_ideas": ["примеры правильного применения"],
  "practical_takeaways": ["антипримеры / чего НЕ делать"],
  "tools": [],
  "technologies": [],
  "links": [],
  "code_blocks": [],
  "category": "EXACT name from Existing categories only, or \"\"",
  "tags": ["2–5 tags"],
  "usefulness_signals": {
    "actionability": 1-5,
    "novelty": 1-5,
    "specificity": 1-5,
    "reuse_likelihood": 1-5,
    "personal_fit": 1-5
  },
  "usefulness_score": 1-10,
  "usefulness_reason": "как правило помогает профилю / снижает повторяющиеся ошибки"
}

Rules:
- main_idea MUST be the rule statement itself.
- Prefer durable guidance over one-off tasks.
- Empty tools/links/code unless the rule names them.
- Respond in Russian when the input is Russian.
""".strip()


class RateLimitError(Exception):
    pass


class SchemaValidationError(Exception):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise SchemaValidationError("No JSON object in model response")
        return json.loads(match.group(0))


import threading

# Process-wide lock serializing legacy google.generativeai.configure(api_key=...) calls
_GEMINI_LOCK = threading.Lock()


class LLMClient(ABC):
    provider: str = ""
    model: str = ""
    key_id: int | str | None = None

    @abstractmethod
    async def complete_json(self, system: str, user: str) -> LLMResult:
        ...

    @abstractmethod
    async def complete_text(self, system: str, user: str) -> str:
        ...

    async def aclose(self) -> None:
        return None


class TranscribeClient(ABC):
    key_id: int | str | None = None

    @abstractmethod
    async def transcribe(self, path: str, language: str | None = None) -> TranscribeResult:
        ...

    async def aclose(self) -> None:
        return None


class GroqLLMClient(LLMClient):
    def __init__(self, settings: Settings, api_key: str, key_id: int | str | None = None):
        from groq import AsyncGroq

        self.settings = settings
        self.key_id = key_id
        self.provider = "groq"
        self.client = AsyncGroq(api_key=api_key)
        self.model = settings.groq_llm_model

    @retry(
        retry=retry_if_exception_type(SchemaValidationError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def complete_json(self, system: str, user: str) -> LLMResult:
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "rate limit" in msg or "quota" in msg:
                raise RateLimitError(str(e)) from e
            if "401" in msg or "invalid api key" in msg or "auth" in msg:
                raise RateLimitError(str(e)) from e
            raise

        content = resp.choices[0].message.content or ""
        data = _extract_json(content)
        try:
            card = KnowledgeCard.model_validate(data)
        except Exception as e:
            raise SchemaValidationError(str(e)) from e

        usage = resp.usage
        return LLMResult(
            card=card,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            provider="groq",
            model=self.model,
        )

    async def complete_text(self, system: str, user: str) -> str:
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "rate limit" in msg or "quota" in msg or "401" in msg:
                raise RateLimitError(str(e)) from e
            raise
        return (resp.choices[0].message.content or "").strip()


    async def aclose(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            res = close()
            if hasattr(res, "__await__"):
                await res


class GoogleLLMClient(LLMClient):
    def __init__(self, settings: Settings, api_key: str, key_id: int | str | None = None):
        if not api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY required")
        self.settings = settings
        self.key_id = key_id
        self.provider = "google"
        self.api_key = api_key
        self.model_name = settings.google_ai_model

    def _generate_sync(self, prompt: str, generation_config: dict[str, Any]):
        import google.generativeai as genai

        with _GEMINI_LOCK:
            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel(self.model_name)
            return model.generate_content(prompt, generation_config=generation_config)

    @retry(
        retry=retry_if_exception_type(SchemaValidationError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def complete_json(self, system: str, user: str) -> LLMResult:
        import asyncio

        prompt = f"{system}\n\n{user}"
        try:
            resp = await asyncio.to_thread(
                self._generate_sync,
                prompt,
                {"temperature": 0.2, "response_mime_type": "application/json"},
            )
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "rate" in msg or "quota" in msg or "api key" in msg:
                raise RateLimitError(str(e)) from e
            raise

        content = getattr(resp, "text", None) or ""
        data = _extract_json(content)
        try:
            card = KnowledgeCard.model_validate(data)
        except Exception as e:
            raise SchemaValidationError(str(e)) from e

        return LLMResult(card=card, tokens_in=0, tokens_out=0, provider="google", model=self.model_name)

    async def complete_text(self, system: str, user: str) -> str:
        import asyncio

        prompt = f"{system}\n\n{user}"
        try:
            resp = await asyncio.to_thread(
                self._generate_sync,
                prompt,
                {"temperature": 0.3},
            )
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "rate" in msg or "quota" in msg or "api key" in msg:
                raise RateLimitError(str(e)) from e
            raise
        return (getattr(resp, "text", None) or "").strip()


class GroqTranscribeClient(TranscribeClient):
    def __init__(self, settings: Settings, api_key: str, key_id: int | str | None = None):
        from groq import AsyncGroq

        self.settings = settings
        self.key_id = key_id
        self.client = AsyncGroq(api_key=api_key)
        self.model = settings.groq_whisper_model

    async def aclose(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            res = close()
            if hasattr(res, "__await__"):
                await res

    @staticmethod
    def _build_prompt(lang: str) -> str:
        if lang == "ru":
            return (
                "Транскрипция разговорной русской речи. "
                "Пиши дословно, сохраняй мат, сленг и междометия. "
                "Не цензурируй и не заменяй слова звёздочками или эвфемизмами. "
                "Названия продуктов оставляй как есть: CapCut, overlay, keyframe, opacity."
            )
        if lang == "en":
            return (
                "Transcribe spoken English faithfully. Keep UI/product terms as CapCut, "
                "overlay, keyframe, opacity, layer, timeline, blend."
            )
        return (
            "Transcribe faithfully in the spoken language. Keep product/UI names: "
            "CapCut, overlay, keyframe, opacity, layer, timeline. "
            "Do not translate technical UI terms into unrelated words."
        )

    @staticmethod
    def _classify_api_error(exc: Exception) -> str:
        """Classify transcription API errors to avoid spurious retries or silent 413 failures."""
        status = getattr(exc, "status_code", None)
        msg = str(exc).lower()
        if (
            status in (401, 429)
            or "401" in msg
            or "invalid api key" in msg
            or "429" in msg
            or "rate limit" in msg
        ):
            return "rate_limit"
        if (
            status == 413
            or "413" in msg
            or "too large" in msg
            or "entity too large" in msg
            or "maximum content size" in msg
        ):
            return "payload_too_large"
        if status in (500, 502, 503, 504) or any(
            tok in msg
            for tok in ("timeout", "timed out", "connection", "502", "503", "504", "500")
        ):
            return "transient"
        return "param"

    @staticmethod
    def _format_timed_segments(segments: list[TranscriptSegment]) -> str:
        timed_lines = []
        for seg in segments:
            m, sec = divmod(int(max(0.0, seg.start)), 60)
            timed_lines.append(f"[{m}:{sec:02d}] {seg.text}")
        return "\n".join(timed_lines)

    async def _call_transcriptions_api(self, kwargs: dict[str, Any]):
        import asyncio

        from app.services.ai_limits import set_key_cooldown, with_whisper_slot

        current = dict(kwargs)
        transient_attempts = 0
        max_transient = max(1, int(getattr(self.settings, "max_api_retries", 3) or 3))
        while True:
            try:
                return await with_whisper_slot(self.client.audio.transcriptions.create(**current))
            except Exception as e:
                kind = self._classify_api_error(e)
                if kind == "rate_limit":
                    if self.key_id is not None:
                        set_key_cooldown(self.key_id)
                    raise RateLimitError(str(e)) from e
                if kind == "payload_too_large":
                    raise
                if kind == "transient":
                    transient_attempts += 1
                    if transient_attempts < max_transient:
                        await asyncio.sleep(min(2.0 * transient_attempts, 5.0))
                        continue
                    raise
                # Older API / models may reject timestamp_granularities or prompt
                if "timestamp_granularities" in current:
                    current.pop("timestamp_granularities", None)
                    continue
                if "prompt" in current:
                    current.pop("prompt", None)
                    continue
                raise

    def _parse_response(self, resp: Any) -> TranscribeResult:
        text = getattr(resp, "text", "") or ""
        duration = float(getattr(resp, "duration", 0) or 0)
        segments: list[TranscriptSegment] = []
        raw_segs = getattr(resp, "segments", None) or []
        for s in raw_segs:
            if isinstance(s, dict):
                start = float(s.get("start") or 0)
                end = float(s.get("end") or start)
                seg_text = str(s.get("text") or "").strip()
            else:
                start = float(getattr(s, "start", 0) or 0)
                end = float(getattr(s, "end", start) or start)
                seg_text = str(getattr(s, "text", "") or "").strip()
            if seg_text:
                segments.append(TranscriptSegment(start=start, end=end, text=seg_text))

        if segments:
            text = self._format_timed_segments(segments)

        detected = getattr(resp, "language", None)
        if detected is not None:
            detected = str(detected).strip().lower() or None

        return TranscribeResult(
            text=text,
            audio_seconds=duration,
            provider="groq",
            model=self.model,
            language=detected,
            segments=segments,
        )

    async def _transcribe_single(
        self,
        path: str,
        *,
        lang: str,
        prompt: str,
    ) -> TranscribeResult:
        import asyncio
        from pathlib import Path

        p = Path(path)
        data = await asyncio.to_thread(p.read_bytes)
        kwargs: dict[str, Any] = {
            "file": (p.name, data),
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
            "temperature": 0,
            "prompt": prompt,
        }
        if lang != "auto":
            kwargs["language"] = lang

        resp = await self._call_transcriptions_api(kwargs)
        return self._parse_response(resp)

    async def _transcribe_chunked(
        self,
        path: str,
        *,
        lang: str,
        prompt: str,
        max_bytes: int,
        chunk_seconds: int,
    ) -> TranscribeResult:
        import tempfile
        from pathlib import Path

        from app.services.media import probe_duration_seconds, split_audio_chunks

        # 64 kbps mono ≈ 8000 bytes/s; leave 20% headroom below max_bytes
        max_sec_for_bytes = max(5, int((max_bytes * 0.8) / 8000))
        effective_chunk_seconds = min(chunk_seconds, max_sec_for_bytes)

        log.info(
            "whisper_chunked_start",
            path=path,
            chunk_seconds=effective_chunk_seconds,
            max_bytes=max_bytes,
        )

        with tempfile.TemporaryDirectory(prefix="chronicum_asr_") as tmp_dir:
            chunks = await split_audio_chunks(
                path,
                tmp_dir,
                chunk_seconds=effective_chunk_seconds,
            )
            all_segments: list[TranscriptSegment] = []
            plain_parts: list[str] = []
            total_audio_seconds = 0.0
            detected_language: str | None = None
            has_text_language = False
            offset = 0.0

            for idx, chunk_path in enumerate(chunks):
                chunk_size = Path(chunk_path).stat().st_size
                if chunk_size > max_bytes:
                    raise RuntimeError(
                        f"Audio chunk {idx + 1}/{len(chunks)} is {chunk_size} bytes "
                        f"(exceeds limit {max_bytes} bytes)"
                    )
                probed_dur = await probe_duration_seconds(chunk_path)
                res = await self._transcribe_single(chunk_path, lang=lang, prompt=prompt)

                chunk_dur = probed_dur or res.audio_seconds or (
                    float(effective_chunk_seconds) if idx < len(chunks) - 1 else 0.0
                )
                total_audio_seconds += res.audio_seconds or probed_dur

                if res.language and (
                    not detected_language or (not has_text_language and res.text.strip())
                ):
                    detected_language = res.language
                    if res.text.strip():
                        has_text_language = True

                if res.segments:
                    for seg in res.segments:
                        all_segments.append(
                            TranscriptSegment(
                                start=round(seg.start + offset, 3),
                                end=round(seg.end + offset, 3),
                                text=seg.text,
                            )
                        )
                elif res.text.strip():
                    plain_parts.append(res.text.strip())
                    all_segments.append(
                        TranscriptSegment(
                            start=round(offset, 3),
                            end=round(offset + chunk_dur, 3),
                            text=res.text.strip(),
                        )
                    )

                offset += chunk_dur

            text = self._format_timed_segments(all_segments) if all_segments else "\n".join(plain_parts)

            log.info(
                "whisper_chunked_done",
                path=path,
                chunks=len(chunks),
                audio_seconds=round(total_audio_seconds, 2),
                segments=len(all_segments),
            )
            return TranscribeResult(
                text=text,
                audio_seconds=total_audio_seconds,
                provider="groq",
                model=self.model,
                language=detected_language,
                segments=all_segments,
            )

    async def transcribe(self, path: str, language: str | None = None) -> TranscribeResult:
        from pathlib import Path

        from app.services.media import probe_duration_seconds

        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        # None → settings.whisper_language; "auto" → let Whisper detect (fixes EN tutorials forced to RU)
        cfg_lang = (getattr(self.settings, "whisper_language", None) or "auto").strip().lower()
        lang = (language if language is not None else cfg_lang).strip().lower() or "auto"
        prompt = self._build_prompt(lang)

        max_mb = float(getattr(self.settings, "whisper_max_file_mb", 20.0) or 20.0)
        max_bytes = max(1024, int(max_mb * 1024 * 1024))
        chunk_seconds = max(5, int(getattr(self.settings, "whisper_chunk_seconds", 600) or 600))

        file_size = p.stat().st_size
        duration = 0.0 if file_size > max_bytes else await probe_duration_seconds(str(p))
        needs_chunking = file_size > max_bytes or (duration > 0 and duration > chunk_seconds)

        if not needs_chunking:
            try:
                return await self._transcribe_single(str(p), lang=lang, prompt=prompt)
            except Exception as e:
                if self._classify_api_error(e) != "payload_too_large":
                    raise
                log.warning(
                    "whisper_413_fallback_to_chunks",
                    path=str(p),
                    size_bytes=file_size,
                )

        return await self._transcribe_chunked(
            str(p),
            lang=lang,
            prompt=prompt,
            max_bytes=max_bytes,
            chunk_seconds=chunk_seconds,
        )


def _client_for(provider: str, api_key: str, settings: Settings, key_id: int | str | None) -> LLMClient:
    if provider == "google":
        return GoogleLLMClient(settings, api_key, key_id=key_id)
    return GroqLLMClient(settings, api_key, key_id=key_id)


async def get_llm_client_pooled(session) -> LLMClient:
    """Groq keys first, then Gemini — rotate through pool on RateLimitError externally."""
    from app.services.key_pool import acquire_keys

    settings = get_settings()
    preferred = settings.ai_provider if settings.ai_provider in {"groq", "google"} else "groq"
    order = [preferred] + [p for p in ("groq", "google") if p != preferred]

    last_err: Exception | None = None
    for provider in order:
        keys = await acquire_keys(session, provider)
        for pk in keys:
            try:
                return _client_for(provider, pk.api_key, settings, pk.id)
            except Exception as e:
                last_err = e
                continue
    raise RuntimeError(last_err or "No API keys in pool — add Groq/Gemini in Settings")


async def with_llm_fallback(session, fn):
    """Call async fn(client); on RateLimitError try next key/provider and close clients cleanly."""
    from app.services.ai_limits import with_llm_slot
    from app.services.key_pool import acquire_keys, mark_key_result

    settings = get_settings()
    preferred = settings.ai_provider if settings.ai_provider in {"groq", "google"} else "groq"
    order = [preferred] + [p for p in ("groq", "google") if p != preferred]
    errors: list[str] = []

    for provider in order:
        keys = await acquire_keys(session, provider)
        for pk in keys:
            client = _client_for(provider, pk.api_key, settings, pk.id)
            try:
                result = await with_llm_slot(fn(client))
                await mark_key_result(session, pk.id, ok=True)
                return result
            except RateLimitError as e:
                errors.append(f"{provider}:{pk.id}: {e}")
                await mark_key_result(session, pk.id, ok=False, error=str(e)[:300])
                continue
            finally:
                try:
                    await client.aclose()
                except Exception:
                    pass
    raise RuntimeError("All API keys exhausted: " + "; ".join(errors[:5]))


async def with_transcribe_fallback(session, fn, *, settings: Settings | None = None):
    """Call async fn(transcribe_client) rotating across all pooled Groq keys (DB + .env)."""
    from app.services.key_pool import acquire_keys, mark_key_result

    cfg = settings or get_settings()
    if cfg.transcribe_provider != "groq":
        raise RuntimeError(f"Unsupported TRANSCRIBE_PROVIDER={cfg.transcribe_provider}")
    keys = await acquire_keys(session, "groq")
    if not keys:
        raise RuntimeError("No Groq keys for transcription — add in Settings or .env")

    errors: list[str] = []
    for pk in keys:
        client = GroqTranscribeClient(cfg, pk.api_key, key_id=pk.id)
        try:
            result = await fn(client)
            await mark_key_result(session, pk.id, ok=True)
            return result
        except RateLimitError as e:
            errors.append(f"groq:{pk.id}: {e}")
            await mark_key_result(session, pk.id, ok=False, error=str(e)[:300])
            continue
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
    raise RuntimeError("All Groq transcription keys exhausted: " + "; ".join(errors[:5]))


class PooledTranscribeClient(TranscribeClient):
    """TranscribeClient delegating each call through key-pool rotation and proper cleanup."""

    def __init__(self, settings: Settings, session=None) -> None:
        self.settings = settings
        self.session = session

    async def transcribe(self, path: str, language: str | None = None) -> TranscribeResult:
        if self.session is not None:
            return await with_transcribe_fallback(
                self.session,
                lambda c: c.transcribe(path, language=language),
                settings=self.settings,
            )
        from app.db import SessionLocal

        async with SessionLocal() as session:
            return await with_transcribe_fallback(
                session,
                lambda c: c.transcribe(path, language=language),
                settings=self.settings,
            )


async def get_transcribe_client_pooled(session) -> GroqTranscribeClient:
    from app.services.key_pool import acquire_keys

    settings = get_settings()
    keys = await acquire_keys(session, "groq")
    if not keys:
        raise RuntimeError("No Groq keys for transcription — add in Settings")
    pk = keys[0]
    return GroqTranscribeClient(settings, pk.api_key, key_id=pk.id)


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    """Sync helper for code paths without session — uses .env only."""
    settings = settings or get_settings()
    if settings.ai_provider == "google" and settings.google_ai_api_key:
        return GoogleLLMClient(settings, settings.google_ai_api_key, key_id="env:google")
    if settings.groq_api_key and settings.groq_api_key.lower() not in _PLACEHOLDERS:
        return GroqLLMClient(settings, settings.groq_api_key, key_id="env:groq")
    if settings.google_ai_api_key:
        return GoogleLLMClient(settings, settings.google_ai_api_key, key_id="env:google")
    raise RuntimeError("No AI API key configured")


_PLACEHOLDERS = {"", "replace_me", "changeme", "xxx"}


def get_transcribe_client(settings: Settings | None = None) -> TranscribeClient:
    settings = settings or get_settings()
    if settings.transcribe_provider != "groq":
        raise RuntimeError(f"Unsupported TRANSCRIBE_PROVIDER={settings.transcribe_provider}")
    key = settings.groq_api_key
    if not key or key.lower() in _PLACEHOLDERS:
        raise RuntimeError("GROQ_API_KEY required for Whisper")
    return GroqTranscribeClient(settings, key, key_id="env:groq")


async def analyze_material(
    raw_text: str,
    *,
    categories: list[str],
    tags: list[str],
    preview: bool = False,
    settings: Settings | None = None,
    session=None,
    personal_profile: dict | None = None,
    screen_resources: str = "",
    entry_kind: str = "knowledge",
    speech_weak: bool = False,
    speech_offtopic: bool = False,
) -> LLMResult:
    settings = settings or get_settings()
    kind = (entry_kind or "knowledge").strip().lower()
    if kind == "task":
        schema_hint = TASK_JSON_SCHEMA_HINT
        role = "Chronicum task extractor. Turn the input into an actionable task card."
        mode = "task card"
    elif kind == "rule":
        schema_hint = RULE_JSON_SCHEMA_HINT
        role = "Chronicum rule extractor. Turn the input into a durable operating rule."
        mode = "rule card"
    else:
        schema_hint = CARD_JSON_SCHEMA_HINT
        role = "Chronicum knowledge extractor."
        mode = "brief preview relevance analysis" if preview else "full knowledge card"

    profile = personal_profile
    if profile is None and session is not None:
        from app.services.key_pool import get_app_settings

        app_cfg = await get_app_settings(session)
        profile = app_cfg.get("personal_profile")

    from app.services.key_pool import format_profile_for_prompt

    profile_block = format_profile_for_prompt(profile)

    system = (
        f"{role} Produce a {mode}. Respond in Russian when source is Russian.\n"
        f"{schema_hint}\n"
        f"{profile_block}\n"
        f"Existing categories (CLOSED LIST — pick exact string or \"\"): "
        f"{', '.join(categories) if categories else '(none yet — leave category empty)'}\n"
        f"Existing approved tags: {', '.join(tags) if tags else '(none yet)'}\n"
        "Category rules: copy an existing category name EXACTLY, or use empty string. "
        "Do NOT create new category names. Do NOT assign a category that does not clearly fit.\n"
        "Reuse existing tag strings exactly when they fit. "
        "Be a strict reviewer on usefulness_signals — generosity and ignoring the personal profile make the archive useless."
    )
    if speech_weak:
        system += (
            "\n\nSPEECH_WEAK: Spoken transcript is missing or Whisper hallucination. "
            "Use caption + on-screen OCR / SCREEN RESOURCES only. "
            "Do NOT invent spoken steps or dialogue. digest: at most 3 short bullets grounded in caption/OCR; "
            "actionability ≤ 3; usefulness_score after calibration will be low-to-mid — keep signals honest."
        )
    elif speech_offtopic:
        system += (
            "\n\nSPEECH_OFFTOPIC: Spoken audio does not match the on-screen topic "
            "(ad jingle / music bed / unrelated voiceover). "
            "Ignore spoken lyrics for tools and digest. "
            "Use caption + on-screen OCR / SCREEN RESOURCES brands only. "
            "Do NOT invent workflows (e.g. After Effects steps) from the audio. "
            "digest: at most 3 short bullets grounded in caption/OCR brands and CTA; "
            "actionability ≤ 3; keep usefulness honest and low-to-mid."
        )
    res_block = (screen_resources or "").strip()
    user = (
        f"{res_block}\n\nMaterial text:\n\n{raw_text[:120000]}"
        if res_block
        else f"Material text:\n\n{raw_text[:120000]}"
    )

    if session is not None:
        async def _run(client: LLMClient) -> LLMResult:
            return await client.complete_json(system, user)

        result = await with_llm_fallback(session, _run)
    else:
        from app.services.ai_limits import with_llm_slot

        client = get_llm_client(settings)
        result = await with_llm_slot(client.complete_json(system, user))

    log.info(
        "ai_analysis_done",
        provider=result.provider,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        preview=preview,
        entry_kind=kind,
        score=result.card.usefulness_score,
        personal_fit=result.card.usefulness_signals.personal_fit,
    )
    return result
