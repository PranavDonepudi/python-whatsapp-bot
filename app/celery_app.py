from celery import Celery
import os

celery_app = Celery(
    "whatsapp_tasks",
    broker=os.getenv("CELERY_BROKER_URL"),
)
