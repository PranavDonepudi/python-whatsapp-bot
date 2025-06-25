import os
from celery import Celery

celery_app = Celery(
    "whatsapp_tasks",
    broker=os.getenv("CELERY_BROKER_URL"),
    include=["app.tasks.tasks"],
)

celery_app.autodiscover_tasks(["app.tasks.tasks"])
# Tell Celery we’re using JSON (recommended) and set a default queue name
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    task_default_queue="whatsapp-celery-queue",
    broker_transport_options={
        "region": os.getenv("AWS_REGION"),
        "queue_name_prefix": "",
        "visibility_timeout": 2000,  # seconds a message is hidden after a worker grabs it
        "polling_interval": 3,  # how often to poll SQS (in seconds)
    },
    enable_utc=True,
    timezone="UTC",
)
