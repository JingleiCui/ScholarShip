from celery import Celery
from scholarship.config import settings

app = Celery(
    "scholarship", broker=settings().broker_url, include=["scholarship.workers.tasks", "scholarship.workers.documents"]
)
app.conf.update(
    task_default_queue=settings().celery_queue,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 120},
    beat_schedule={"outbox": {"task": "scholarship.dispatch", "schedule": 2.0}},
    timezone="UTC",
    enable_utc=True,
    worker_send_task_events=False,
)
