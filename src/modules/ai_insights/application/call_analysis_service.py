"""Call analysis: recording -> transcript -> review against the checklist.

Started by an answered, recorded call from the PBX (no user) or by a user who can see
the call (e.g. again in Kyrgyz). Each worker step is idempotent and advances one state;
the recording goes to the AI Platform, which deletes it within its retention window,
and the CRM asks for its deletion as soon as the transcript is in.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.ai_insights.application.ports import AiJobState, AiPlatformClient
from modules.ai_insights.application.service import AiDisabledError, poll_delay
from modules.ai_insights.domain.call_analysis import (
    DEFAULT_CRITERIA,
    REVIEW_JOB,
    REVIEW_SCHEMA,
    TRANSCRIBE_JOB,
    TRANSCRIPT_SCHEMA,
    AnalysisStatus,
    CallAnalysis,
    check_criteria,
)
from shared.application.ports import UnitOfWork
from shared.application.retry import PermanentError
from shared.domain.errors import NotFoundError

logger = logging.getLogger(__name__)


class AnalysisRepository(Protocol):
    def add(self, analysis: CallAnalysis) -> None: ...

    def get(self, analysis_id: UUID) -> CallAnalysis | None: ...

    def latest_for(self, call_id: UUID) -> CallAnalysis | None: ...

    def save(self, analysis: CallAnalysis, expected_version: int) -> None: ...


class CriteriaStore(Protocol):
    def get(self) -> list[dict[str, str]] | None: ...

    def save(self, criteria: list[dict[str, str]]) -> None: ...


class CallAudio(Protocol):
    def recording(self, call_id: UUID) -> tuple[bytes, str]:
        """(audio, content type); NotFoundError without a recording."""
        ...

    def defaults(self) -> tuple[str, str]:
        """(language, channels) the company set for its calls."""
        ...

    def check_visible(self, principal: Principal, call_id: UUID) -> None:
        """NotFoundError if the principal cannot see the call."""
        ...


class AudioPlatform(AiPlatformClient, Protocol):
    def put_audio(
        self, *, tenant_slug: str, subject: str, ref: str, data: bytes, content_type: str
    ) -> None: ...

    def delete_audio(self, *, tenant_slug: str, subject: str, ref: str) -> None: ...


class AnalysisScheduler(Protocol):
    def schedule_step(
        self, *, tenant_slug: str, analysis_id: UUID, delay_seconds: float
    ) -> None: ...


class CallAnalysisService:
    def __init__(
        self,
        repository: AnalysisRepository,
        criteria: CriteriaStore,
        audio: CallAudio,
        client: AudioPlatform,
        scheduler: AnalysisScheduler,
        uow: UnitOfWork,
        *,
        tenant_slug: str,
        enabled: bool,
        deadline_seconds: float = 1800.0,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._criteria = criteria
        self._audio = audio
        self._client = client
        self._scheduler = scheduler
        self._uow = uow
        self._tenant_slug = tenant_slug
        self._enabled = enabled
        self._deadline = deadline_seconds
        self._policy = policy or AccessPolicy()

    # --- starting -------------------------------------------------------------------

    def start(
        self, call_id: UUID, *, language: str | None = None, requested_by: UUID | None = None
    ) -> CallAnalysis | None:
        """System path (the PBX reported an answered call). None while AI is off."""
        if not self._enabled:
            return None
        default_language, channels = self._audio.defaults()
        with self._uow:
            current = self._repo.latest_for(call_id)
            if current is not None and not current.is_finished and requested_by is None:
                return current
            analysis = CallAnalysis.start(
                call_id=call_id,
                language=language or default_language,
                channels=channels,
                requested_by=requested_by,
            )
            self._repo.add(analysis)
            self._scheduler.schedule_step(
                tenant_slug=self._tenant_slug, analysis_id=analysis.id, delay_seconds=0
            )
        return analysis

    def request(self, principal: Principal, call_id: UUID, *, language: str | None) -> CallAnalysis:
        if not self._enabled:
            raise AiDisabledError("AI features are not enabled for this installation.")
        self._policy.require(principal, Permission.AI_REQUEST)
        self._audio.check_visible(principal, call_id)
        analysis = self.start(call_id, language=language, requested_by=principal.user_id)
        if analysis is None:
            raise AiDisabledError("AI features are not enabled for this installation.")
        return analysis

    def latest(self, principal: Principal, call_id: UUID) -> CallAnalysis:
        self._audio.check_visible(principal, call_id)
        analysis = self._repo.latest_for(call_id)
        if analysis is None:
            raise NotFoundError("The call has not been analysed.")
        return analysis

    # --- checklist --------------------------------------------------------------------

    def checklist(self, language: str = "ru") -> list[dict[str, str]]:
        return self._criteria.get() or DEFAULT_CRITERIA.get(language, DEFAULT_CRITERIA["ru"])

    def set_checklist(
        self, principal: Principal, criteria: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        self._policy.require(principal, Permission.COMPANY_SETTINGS_MANAGE)
        checked = check_criteria(criteria)
        with self._uow:
            self._criteria.save(checked)
        return checked

    # --- worker step (idempotent) -------------------------------------------------------

    def step(self, analysis_id: UUID) -> None:
        analysis = self._repo.get(analysis_id)
        if analysis is None or analysis.is_finished:
            return
        subject = f"call:{analysis.call_id}"
        try:
            if analysis.status is AnalysisStatus.PENDING:
                self._upload_and_transcribe(analysis, subject)
            elif analysis.status is AnalysisStatus.TRANSCRIBING:
                self._after_transcription(analysis, subject)
            elif analysis.status is AnalysisStatus.REVIEWING:
                self._after_review(analysis, subject)
        except NotFoundError:
            # No recording any more (deleted in the PBX, or the PBX was disconnected).
            self._save(analysis, lambda a: a.failed("no_recording"))
        except PermanentError:
            logger.warning("call_analysis_failed", extra={"analysis_id": str(analysis.id)})
            self._save(analysis, lambda a: a.failed("rejected"))

    def _upload_and_transcribe(self, analysis: CallAnalysis, subject: str) -> None:
        data, content_type = self._audio.recording(analysis.call_id)
        self._client.put_audio(
            tenant_slug=self._tenant_slug,
            subject=subject,
            ref=analysis.audio_ref,
            data=data,
            content_type=content_type,
        )
        state = self._client.submit(
            tenant_slug=self._tenant_slug,
            subject=subject,
            job_type=TRANSCRIBE_JOB,
            payload={
                "audio_ref": analysis.audio_ref,
                "language": analysis.language,
                "channels": analysis.channels,
                "call_ref": f"call:{analysis.call_id}",
            },
            idempotency_key=f"crm-transcribe-{analysis.id}",
        )
        self._save(analysis, lambda a: a.transcribing(state.job_id), poll=2.0)

    def _after_transcription(self, analysis: CallAnalysis, subject: str) -> None:
        state = self._fetch(analysis, analysis.transcribe_job_id, subject)
        if state is None:
            return
        if (
            state.status != "succeeded"
            or state.output is None
            or state.schema_id != TRANSCRIPT_SCHEMA
        ):
            self._save(analysis, lambda a: a.failed(state.error_code or "transcribe_failed"))
            return
        self._forget_audio(analysis, subject)
        output = state.output
        has_speech = analysis.transcribed(output, is_mock=_is_mock(output))
        if not has_speech:
            self._save(analysis, lambda a: None)
            return
        payload = analysis.review_payload(self.checklist(analysis.language))
        review = self._client.submit(
            tenant_slug=self._tenant_slug,
            subject=subject,
            job_type=REVIEW_JOB,
            payload=payload,
            idempotency_key=f"crm-review-{analysis.id}",
        )
        self._save(analysis, lambda a: a.reviewing(review.job_id), poll=2.0)

    def _after_review(self, analysis: CallAnalysis, subject: str) -> None:
        state = self._fetch(analysis, analysis.review_job_id, subject)
        if state is None:
            return
        if state.status != "succeeded" or state.output is None or state.schema_id != REVIEW_SCHEMA:
            self._save(analysis, lambda a: a.failed(state.error_code or "review_failed"))
            return
        output = state.output
        self._save(
            analysis, lambda a: a.reviewed(output, is_mock=_is_mock(output), model=state.model)
        )

    # --- helpers ----------------------------------------------------------------------

    def _fetch(self, analysis: CallAnalysis, job_id: str | None, subject: str) -> AiJobState | None:
        """The finished job, or None after scheduling another look."""
        if not job_id:
            raise PermanentError("The analysis lost its job id.")
        state = self._client.fetch(tenant_slug=self._tenant_slug, subject=subject, job_id=job_id)
        if state.is_terminal:
            return state
        age = (datetime.now(UTC) - analysis.created_at).total_seconds()
        if age > self._deadline:
            self._save(analysis, lambda a: a.failed("timeout"))
            return None
        self._scheduler.schedule_step(
            tenant_slug=self._tenant_slug, analysis_id=analysis.id, delay_seconds=poll_delay(age)
        )
        return None

    def _forget_audio(self, analysis: CallAnalysis, subject: str) -> None:
        try:
            self._client.delete_audio(
                tenant_slug=self._tenant_slug, subject=subject, ref=analysis.audio_ref
            )
        except Exception:  # noqa: BLE001 - the platform also deletes it on its own schedule
            logger.info("call_audio_delete_deferred", extra={"analysis_id": str(analysis.id)})

    def _save(self, analysis: CallAnalysis, change: Any, *, poll: float | None = None) -> None:
        with self._uow:
            version = analysis.version
            change(analysis)
            self._repo.save(analysis, expected_version=version)
            if poll is not None:
                self._scheduler.schedule_step(
                    tenant_slug=self._tenant_slug, analysis_id=analysis.id, delay_seconds=poll
                )


def _is_mock(output: dict[str, Any]) -> bool:
    generator = output.get("generator")
    # Unknown provenance is treated as mock, never as a real model.
    return not isinstance(generator, dict) or generator.get("is_mock") is not False
