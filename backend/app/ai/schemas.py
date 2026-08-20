from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.resources import digest_lines, jaccard, token_set


class UsefulnessSignals(BaseModel):
    """1–5 dims; score is derived so the model can't default everything to 8."""

    actionability: int = Field(..., ge=1, le=5, description="How concrete/actionable")
    novelty: int = Field(..., ge=1, le=5, description="New vs well-known")
    specificity: int = Field(..., ge=1, le=5, description="Concrete details vs vague advice")
    reuse_likelihood: int = Field(..., ge=1, le=5, description="Will user reuse this soon")
    personal_fit: int = Field(
        default=3,
        ge=1,
        le=5,
        description="How well this matches the user's personal profile (3=neutral/empty profile)",
    )


_DEFAULT_PRINCIPLES = [
    "Удержание внимания важнее самого монтажа",
    "B-roll должен совпадать с речью",
    "Конкретные инструменты ускоряют рабочий процесс",
    "Цвет и текстура — финальный слой качества",
]

_ALLOWED_CODE_LANGS = {
    "python",
    "py",
    "javascript",
    "js",
    "typescript",
    "ts",
    "bash",
    "shell",
    "sh",
    "css",
    "html",
    "sql",
    "json",
    "yaml",
    "yml",
    "text",
    "markdown",
    "md",
    "go",
    "rust",
    "java",
    "swift",
    "kotlin",
    "c",
    "cpp",
    "csharp",
    "php",
    "ruby",
    "r",
}


class LinkItem(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    title: str = Field(default="", max_length=200)
    source: str = Field(default="", max_length=40)

    @field_validator("url", "title", "source", mode="before")
    @classmethod
    def _strip_str(cls, v: Any) -> str:
        return str(v or "").strip()


class CodeBlock(BaseModel):
    title: str = Field(default="", max_length=120)
    language: str = Field(default="text", max_length=40)
    code: str = Field(..., min_length=1, max_length=4000)

    @field_validator("title", "language", mode="before")
    @classmethod
    def _strip_meta(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("code", mode="before")
    @classmethod
    def _normalize_code(cls, v: Any) -> str:
        return str(v or "").replace("\r\n", "\n").strip()

    @field_validator("language", mode="after")
    @classmethod
    def _canon_lang(cls, v: str) -> str:
        lang = (v or "text").lower()
        aliases = {"py": "python", "js": "javascript", "ts": "typescript", "sh": "bash", "shell": "bash", "yml": "yaml", "md": "markdown"}
        lang = aliases.get(lang, lang)
        if lang not in _ALLOWED_CODE_LANGS:
            return "text"
        return lang


class KnowledgeCard(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    summary: str = Field(..., min_length=1)
    main_idea: str = Field(..., min_length=1)
    digest: str = Field(default="", description="Ultra-short lazy digest, 3-6 bullets or ~800 chars")
    key_ideas: list[str] = Field(default_factory=list)
    practical_takeaways: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    links: list[LinkItem] = Field(default_factory=list)
    code_blocks: list[CodeBlock] = Field(default_factory=list)
    category: str = Field(default="", description="Existing category name or empty")
    tags: list[str] = Field(default_factory=list)
    usefulness_signals: UsefulnessSignals
    usefulness_score: int = Field(default=5, ge=1, le=10)
    usefulness_reason: str = Field(..., min_length=1)

    @field_validator("category", mode="before")
    @classmethod
    def empty_category_ok(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("tags", "key_ideas", "practical_takeaways", "tools", "technologies", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return list(v)

    @field_validator("links", mode="before")
    @classmethod
    def normalize_links(cls, v: Any) -> list:
        from app.services.resources import coerce_link_items

        return coerce_link_items(v)

    @field_validator("code_blocks", mode="before")
    @classmethod
    def normalize_code_blocks(cls, v: Any) -> list:
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        if isinstance(v, str):
            s = v.strip()
            return [{"title": "", "language": "text", "code": s}] if s else []
        return list(v)

    @model_validator(mode="after")
    def calibrate_and_dedupe(self) -> "KnowledgeCard":
        self._dedupe_fields()
        self._calibrate_usefulness()
        return self

    def _dedupe_fields(self) -> None:
        dig_lines = digest_lines(self.digest)
        dig_sets = [token_set(x) for x in dig_lines]
        dig_union = set().union(*dig_sets) if dig_sets else token_set(self.digest)

        # Drop key_ideas that largely copy a digest bullet
        cleaned_ideas: list[str] = []
        for idea in self.key_ideas or []:
            idea_s = str(idea).strip()
            if not idea_s:
                continue
            its = token_set(idea_s)
            if any(jaccard(its, ds) >= 0.55 for ds in dig_sets if ds):
                continue
            if dig_union and jaccard(its, dig_union) >= 0.65:
                continue
            # Near-duplicate of another idea
            if any(jaccard(its, token_set(x)) >= 0.7 for x in cleaned_ideas):
                continue
            cleaned_ideas.append(idea_s)

        if len(cleaned_ideas) < 2 and dig_lines:
            dig_l = (self.digest or "").lower()
            mediaish = any(
                t in dig_l for t in ("b-roll", "монтаж", "кадр", "ролик", "видео", "reel", "тикток")
            )
            if mediaish:
                for p in _DEFAULT_PRINCIPLES:
                    if p not in cleaned_ideas:
                        cleaned_ideas.append(p)
                    if len(cleaned_ideas) >= 3:
                        break
        self.key_ideas = cleaned_ideas[:6]

        # Soft-dedupe takeaways vs digest
        cleaned_ta: list[str] = []
        for ta in self.practical_takeaways or []:
            t = str(ta).strip()
            if not t:
                continue
            ts = token_set(t)
            if any(jaccard(ts, ds) >= 0.75 for ds in dig_sets if ds):
                continue
            cleaned_ta.append(t)
        self.practical_takeaways = cleaned_ta[:8]

        # Dedupe links by normalized URL
        from app.services.resources import merge_link_items

        self.links = [LinkItem.model_validate(x) for x in merge_link_items([li.model_dump() for li in self.links])]

        # Cap code blocks
        self.code_blocks = (self.code_blocks or [])[:8]

        # If main_idea ≈ first digest line, keep but trim list echo
        mi = token_set(self.main_idea)
        if dig_sets and any(jaccard(mi, ds) >= 0.8 for ds in dig_sets):
            # Prefer a thesis-style main idea when model dumped a step
            if self.summary:
                # leave as-is; summary will carry context
                pass

    def _calibrate_usefulness(self) -> None:
        s = self.usefulness_signals
        # Base quality from 4 signals (personal_fit applied after)
        w = (
            s.actionability * 0.35
            + s.reuse_likelihood * 0.30
            + s.specificity * 0.25
            + s.novelty * 0.10
        )  # 1..5
        base = 1.0 + (w - 1.0) * (9.0 / 4.0)  # → ~1..10
        score_i = int(round(base))

        # Quality caps: average / vague content must not land at 8+ on base alone
        if s.actionability <= 2 or s.specificity <= 2:
            score_i = min(score_i, 6)
        if s.actionability <= 3 and s.reuse_likelihood <= 3:
            score_i = min(score_i, 7)
        if score_i >= 8 and (s.actionability < 4 or s.reuse_likelihood < 4):
            score_i = 7
        if score_i >= 9 and (s.actionability < 5 or s.specificity < 4):
            score_i = 8
        if score_i >= 10 and min(s.actionability, s.reuse_likelihood, s.specificity) < 5:
            score_i = 9

        # Personal fit delta: (fit-3)*2.5 → -5..+5
        fit = int(s.personal_fit)
        delta = int(round((fit - 3) * 2.5))
        delta = max(-5, min(5, delta))
        score_i = score_i + delta

        # Hard personal caps (off-topic / on-topic)
        if fit <= 2:
            score_i = min(score_i, 5)
        if fit == 1:
            score_i = min(score_i, 3)
        if fit >= 5 and s.actionability >= 4:
            score_i = max(score_i, 7)

        self.usefulness_score = max(1, min(10, score_i))


class LLMResult(BaseModel):
    card: KnowledgeCard
    tokens_in: int = 0
    tokens_out: int = 0
    provider: str = ""
    model: str = ""


class TranscriptSegment(BaseModel):
    start: float = 0.0
    end: float = 0.0
    text: str = ""


class TranscribeResult(BaseModel):
    text: str
    audio_seconds: float = 0.0
    provider: str = ""
    model: str = ""
    language: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
