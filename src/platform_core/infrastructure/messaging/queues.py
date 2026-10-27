"""RabbitMQ queue topology for Celery.

Every work queue dead-letters into ``<queue>.dlq`` via the ``crm.dlx`` exchange.
A message ends up there when a task finally fails (acks_late +
acks_on_failure_or_timeout=False makes Celery reject it with requeue=False).
"""

from __future__ import annotations

from kombu import Exchange, Queue

WORK_QUEUES = (
    "default",  # short generic tasks
    "outbox",  # outbox relay
    "events",  # domain event dispatch to handlers
    "integrations",  # webhooks and outbound calls to external providers
    "notifications",  # e-mail / push / messenger notifications
    "reports",  # heavy, slow report generation and exports
    "diagnostics",  # health probes and test tasks
)
DEAD_LETTER_EXCHANGE = Exchange("crm.dlx", type="direct", durable=True)


def dead_letter_queue_name(queue: str) -> str:
    return f"{queue}.dlq"


def build_task_queues() -> tuple[Queue, ...]:
    return tuple(
        Queue(
            name,
            Exchange(name, type="direct", durable=True),
            routing_key=name,
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE.name,
                "x-dead-letter-routing-key": dead_letter_queue_name(name),
            },
        )
        for name in WORK_QUEUES
    )


def build_dead_letter_queues() -> tuple[Queue, ...]:
    return tuple(
        Queue(
            dead_letter_queue_name(name),
            DEAD_LETTER_EXCHANGE,
            routing_key=dead_letter_queue_name(name),
            durable=True,
        )
        for name in WORK_QUEUES
    )
