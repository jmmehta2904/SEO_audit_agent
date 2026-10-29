"""JSON-serializable facts, untrusted AI proposals, and evidence-checked reports."""

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt,
    field_validator, model_validator,
)


Severity = Literal["high", "medium", "low"]
AICategory = Literal["title_quality", "keyword_stuffing", "heading_quality", "cta_quality"]
MAX_AI_ISSUES = 30
Count = Annotated[int, Field(strict=True, ge=0)]


class Facts(BaseModel):
    """Objective measurements plus bounded page-derived text samples.

    Lengths and counts refer to the full parsed page, before sample truncation.
    Text is untrusted even when stored in this model.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    title_length: NonNegativeInt = 0
    meta_description: str = ""
    meta_description_length: NonNegativeInt = 0
    h1_count: NonNegativeInt = 0
    headings: dict[str, list[str]] = Field(
        default_factory=lambda: {f"h{level}": [] for level in range(1, 7)}
    )
    image_count: NonNegativeInt = 0
    images_without_alt: NonNegativeInt = 0
    alt_texts: list[str] = Field(default_factory=list)
    link_texts: list[str] = Field(default_factory=list)
    canonical_url: str = ""
    viewport: str = ""
    html_lang: str = ""
    word_count: NonNegativeInt = 0
    open_graph_count: NonNegativeInt = 0


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: Literal["code check", "AI, verified"]
    severity: Severity
    title: str
    explanation: str
    evidence: str
    fix: str
    category: AICategory | None = None


class Issue(BaseModel):
    """A proposal, not a finding. Supporting evidence still needs verification."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
                              revalidate_instances="always")

    category: AICategory
    severity: Severity
    title: str = Field(min_length=1, max_length=160)
    explanation: str = Field(min_length=1, max_length=1500)
    fix: str = Field(min_length=1, max_length=1500)
    evidence_type: Literal["quote", "metric"]
    evidence_quote: str | None = Field(default=None, min_length=1, max_length=2000)
    fact_key: str | None = Field(default=None, min_length=1, max_length=64)
    fact_value: str | int | float | bool | None = None

    @field_validator("title", "explanation", "fix", "evidence_quote", "fact_key")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Text fields must not be blank.")
        return value

    @model_validator(mode="after")
    def evidence_fields_match_mode(self) -> "Issue":
        if self.evidence_type == "quote":
            if self.evidence_quote is None or self.fact_key is not None or self.fact_value is not None:
                raise ValueError("Quote evidence requires only a non-empty evidence_quote.")
        elif self.fact_key is None or self.fact_value is None or self.evidence_quote is not None:
            raise ValueError("Metric evidence requires only fact_key and a non-null fact_value.")
        return self


class Audit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    issues: list[Issue] = Field(max_length=MAX_AI_ISSUES)


class VerificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    proposed: NonNegativeInt
    verified: NonNegativeInt
    dropped: NonNegativeInt

    @model_validator(mode="after")
    def counts_balance(self) -> "VerificationSummary":
        if self.proposed != self.verified + self.dropped:
            raise ValueError("Proposed count must equal verified plus dropped.")
        return self


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding]
    summary: VerificationSummary

    @model_validator(mode="after")
    def findings_match_summary(self) -> "VerificationResult":
        if len(self.findings) != self.summary.verified or any(
            finding.source != "AI, verified" or finding.category is None for finding in self.findings
        ):
            raise ValueError("Verified findings must match their source, category, and count.")
        return self


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    facts: Facts
    findings: list[Finding]
    severity_counts: dict[Severity, Count]
    verification: VerificationSummary | None
    ai_review_available: bool
    ai_notice: str | None
    generated_at: AwareDatetime
    model_name: str = Field(min_length=1)
    adk_version: str = Field(min_length=1)
    limitations: list[str]
    review_mode: Literal["live", "stub"] = "live"

    @model_validator(mode="after")
    def report_is_consistent(self) -> "Report":
        expected = {severity: sum(f.severity == severity for f in self.findings)
                    for severity in ("high", "medium", "low")}
        if self.severity_counts != expected:
            raise ValueError("Severity counts must match the report findings.")
        if self.url != self.facts.url:
            raise ValueError("Report URL must match the audited facts.")
        ai_findings = [f for f in self.findings if f.source == "AI, verified"]
        if self.ai_review_available:
            if self.verification is None or self.ai_notice is not None:
                raise ValueError("Completed AI review requires verification counts and no failure notice.")
            if len(ai_findings) != self.verification.verified or any(f.category is None for f in ai_findings):
                raise ValueError("AI findings must match the verified count and have a category.")
        elif self.verification is not None or ai_findings or self.ai_notice != "AI review unavailable":
            raise ValueError("Unavailable AI review must have a notice, no AI findings, and unknown counts.")
        return self
