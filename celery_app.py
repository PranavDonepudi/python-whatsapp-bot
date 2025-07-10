# celery_app.py
from celery import Celery
import os

BROKER_URL = os.getenv("CELERY_BROKER_URL", "sqs://")
REGION = os.getenv("AWS_REGION", "us-east-2")

app = Celery("whatsapp_worker", broker=BROKER_URL)

# AWS SQS specific settings
app.conf.update(
    broker_transport_options={
        "region": REGION,
        "queue_name_prefix": "celery-",
        "visibility_timeout": 3600,
    },
    task_default_queue="whatsapp-bot",
    accept_content=["json"],
    task_serializer="json",
)
