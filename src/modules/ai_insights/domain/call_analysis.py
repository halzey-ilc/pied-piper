# ruff: noqa: RUF001
"""The CallAnalysis aggregate: a recorded call turned into a transcript (AI Platform
call.transcribe.v1) and a review against the company's checklist (call.review.v1).

    pending -> transcribing -> reviewing -> succeeded
         \\------------\\------------\\-----> failed

The score is computed here from the verdicts, never by the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError

TRANSCRIBE_JOB = "call.transcribe.v1"
REVIEW_JOB = "call.review.v1"
TRANSCRIPT_SCHEMA = "call_transcript.output.v1"
REVIEW_SCHEMA = "call_review.output.v1"
LANGUAGES = ("ru", "ky")
MAX_SEGMENTS = 400
MAX_SEGMENT_TEXT = 2000
MAX_CRITERIA = 15
# Points per verdict; not_applicable is left out of the score.
_POINTS = {"met": 2, "partial": 1, "missed": 0}


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    TRANSCRIBING = "transcribing"
    REVIEWING = "reviewing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


DEFAULT_CRITERIA: dict[str, list[dict[str, str]]] = {
    "ru": [
        {
            "code": "greeting",
            "title": "Приветствие и представление",
            "description": "Поздоровался, назвал себя и компанию",
        },
        {
            "code": "needs",
            "title": "Выявление потребностей",
            "description": "Задаёт открытые вопросы о задачах клиента",
        },
        {
            "code": "presentation",
            "title": "Презентация под задачу",
            "description": "Предлагает решение под названную потребность",
        },
        {
            "code": "objections",
            "title": "Работа с возражениями",
            "description": "Отвечает на сомнения клиента по существу",
        },
        {
            "code": "next_step",
            "title": "Договорённость о следующем шаге",
            "description": "Конкретная дата и действие",
        },
    ],
    "ky": [
        {
            "code": "greeting",
            "title": "Саламдашуу жана таанышуу",
            "description": "Саламдашты, өзүн жана компанияны атады",
        },
        {
            "code": "needs",
            "title": "Муктаждыкты аныктоо",
            "description": "Кардардын маселелери жөнүндө ачык суроолорду берет",
        },
        {
            "code": "presentation",
            "title": "Маселеге ылайык сунуш",
            "description": "Аталган муктаждыкка чечим сунуштайт",
        },
        {
            "code": "objections",
            "title": "Каршы пикирлер менен иштөө",
            "description": "Кардардын шектенүүлөрүнө маңызы боюнча жооп берет",
        },
        {
            "code": "next_step",
            "title": "Кийинки кадам боюнча макулдашуу",
            "description": "Так күн жана аракет",
        },
    ],
}

_SPEAKERS = {
    "ru": {"manager": "Менеджер", "customer": "Клиент", "unknown": "Собеседник"},
    "ky": {"manager": "Менеджер", "customer": "Кардар", "unknown": "Маектеш"},
}


def check_criteria(criteria: list[dict[str, str]]) -> list[dict[str, str]]:
    if not 1 <= len(criteria) <= MAX_CRITERIA:
        raise DomainValidationError(f"A checklist has 1 to {MAX_CRITERIA} points.")
    codes = [c.get("code", "") for c in criteria]
    if len(set(codes)) != len(codes):
        raise DomainValidationError("Checklist point codes must be unique.")
    out = []
    for c in criteria:
        title = str(c.get("title", "")).strip()[:100]
        if not title:
            raise DomainValidationError("Every checklist point needs a title.")
        item = {"code": str(c["code"]), "title": title}
        description = str(c.get("description", "") or "").strip()[:300]
        if description:
            item["description"] = description
        out.append(item)
    return out


def score(verdicts: list[dict[str, Any]]) -> int | None:
    """0..100 from the statuses; None when no point applied to the call."""
    points = [_POINTS[v["status"]] for v in verdicts if v.get("status") in _POINTS]
    if not points:
        return None
    return round(100 * sum(points) / (2 * len(points)))


def transcript_text(segments: list[dict[str, Any]], language: str) -> str:
    """The transcript as text for the call summary: one line per turn, with the speaker."""
    names = _SPEAKERS.get(language, _SPEAKERS["ru"])
    return "\n".join(f"{names.get(s['speaker'], names['unknown'])}: {s['text']}" for s in segments)


@dataclass
class CallAnalysis:
    id: UUID
    call_id: UUID
    language: str
    channels: str
    status: AnalysisStatus
    created_at: datetime
    requested_by: UUID | None = None  # None: started by the call itself
    transcribe_job_id: str | None = None
    review_job_id: str | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float | None = None
    criteria: list[dict[str, str]] = field(default_factory=list)
    review: dict[str, Any] | None = None
    score: int | None = None
    is_mock: bool = False
    model: str | None = None
    error_code: str | None = None
    finished_at: datetime | None = None
    version: int = 1

    @classmethod
    def start(
        cls, *, call_id: UUID, language: str, channels: str, requested_by: UUID | None
    ) -> CallAnalysis:
        if language not in LANGUAGES:
            raise DomainValidationError("Call language must be ru or ky.")
        return cls(
            id=uuid4(),
            call_id=call_id,
            language=language,
            channels=channels,
            status=AnalysisStatus.PENDING,
            created_at=datetime.now(UTC),
            requested_by=requested_by,
        )

    @property
    def audio_ref(self) -> str:
        return f"call-{self.id}"

    @property
    def is_finished(self) -> bool:
        return self.status in (AnalysisStatus.SUCCEEDED, AnalysisStatus.FAILED)

    def transcribing(self, job_id: str) -> None:
        if self.status is not AnalysisStatus.PENDING:
            raise DomainValidationError("Only a pending analysis starts transcribing.")
        self.transcribe_job_id, self.status = job_id, AnalysisStatus.TRANSCRIBING

    def transcribed(self, output: dict[str, Any], *, is_mock: bool) -> bool:
        """Store the transcript; False when there is nothing to review (silence, no speech)."""
        if self.status is not AnalysisStatus.TRANSCRIBING:
            raise DomainValidationError("The analysis is not transcribing.")
        self.segments = [
            {
                "id": f"s{i + 1}",
                "speaker": str(s.get("speaker") or "unknown"),
                "text": str(s.get("text", "")).strip()[:MAX_SEGMENT_TEXT],
                "start_s": s.get("start_s"),
                "end_s": s.get("end_s"),
            }
            for i, s in enumerate(output.get("segments", [])[:MAX_SEGMENTS])
            if str(s.get("text", "")).strip()
        ]
        self.duration_s = output.get("duration_s")
        self.is_mock = self.is_mock or is_mock
        if not self.segments or output.get("insufficient_data"):
            self._finish(AnalysisStatus.SUCCEEDED)
            return False
        return True

    def review_payload(self, criteria: list[dict[str, str]]) -> dict[str, Any]:
        self.criteria = criteria
        return {
            "language": self.language,
            "call_ref": f"call:{self.call_id}",
            "segments": [
                {"id": s["id"], "speaker": s["speaker"], "text": s["text"]} for s in self.segments
            ],
            "criteria": criteria,
        }

    def reviewing(self, job_id: str) -> None:
        if self.status is not AnalysisStatus.TRANSCRIBING:
            raise DomainValidationError("Only a transcribed analysis starts reviewing.")
        self.review_job_id, self.status = job_id, AnalysisStatus.REVIEWING

    def reviewed(self, output: dict[str, Any], *, is_mock: bool, model: str | None) -> None:
        if self.status is not AnalysisStatus.REVIEWING:
            raise DomainValidationError("The analysis is not reviewing.")
        titles = {c["code"]: c["title"] for c in self.criteria}
        verdicts = [
            {**v, "title": titles.get(v.get("code", ""), v.get("code", ""))}
            for v in output.get("criteria", [])
        ]
        self.review = {
            "criteria": verdicts,
            "strengths": [a.get("text", "") for a in output.get("strengths", [])],
            "improvements": [a.get("text", "") for a in output.get("improvements", [])],
            "insufficient_data": bool(output.get("insufficient_data")),
        }
        self.score = score(verdicts)
        self.is_mock, self.model = self.is_mock or is_mock, model
        self._finish(AnalysisStatus.SUCCEEDED)

    def failed(self, error_code: str) -> None:
        if not self.is_finished:
            self.error_code = error_code[:64]
            self._finish(AnalysisStatus.FAILED)

    def _finish(self, status: AnalysisStatus) -> None:
        self.status = status
        self.finished_at = datetime.now(UTC)
