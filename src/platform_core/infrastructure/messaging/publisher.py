"""MessagePublisher adapter: hands outbox events to the Celery ``events`` queue.

Publisher confirms are enabled (CELERY_BROKER_TRANSPORT_OPTIONS.confirm_publish),
so ``publish`` raises when RabbitMQ does not accept the message and the outbox
row stays pending for a retry.
"""

from __future__ import annotations

from celery import current_app

from platform_core.application.ports import EventEnvelope

DISPATCH_TASK = "platform.events.dispatch"


class CeleryMessagePublisher:
    def publish(self, envelope: EventEnvelope) -> None:
        current_app.send_task(
            DISPATCH_TASK,
            kwargs={"envelope": envelope.as_message()},
            queue="events",
            headers={"correlation_id": envelope.correlation_id or ""},
        )
