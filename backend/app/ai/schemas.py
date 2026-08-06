from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


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


class KnowledgeCard(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    summary: str = Field(..., min_length=1)
    main_idea: str = Field(..., min_length=1)
    digest: str = Field(default="", description="Ultra-short lazy digest, 3-6 bullets or ~800 chars")
    key_ideas: list[str] = Field(default_factory=list)
    practical_takeaways: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    category: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    usefulness_signals: UsefulnessSignals
    usefulness_score: int = Field(default=5, ge=1, le=10)
    usefulness_reason: str = Field(..., min_length=1)

    @field_validator("tags", "key_ideas", "practical_takeaways", "tools", "technologies", "links", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return list(v)

    @model_validator(mode="after")
    def calibrate_usefulness(self) -> "KnowledgeCard":
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
        return self


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
    segments: list[TranscriptSegment] = Field(default_factory=list)
