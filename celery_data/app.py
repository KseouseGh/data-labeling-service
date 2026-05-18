from celery import Celery
import config

celery_app = Celery(
    "nli_worker",
    broker=config.REDIS_URL, # Redis as message brocker!
    include=["celery_data.tasks"]
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_ignore_result=True, # Status of task only in session-data!
    broker_transport_options={"visibility_timeout": 3600} # Task's visibility timeout = !
)