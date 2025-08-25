# app/tasks/background_tasks.py
from celery_app import app
from app.services.whatsapp_service import (
    download_whatsapp_media,
    save_file_to_s3,
)
from app.services.dynamodb import save_thread, save_message
import logging


@app.task
def store_message_to_dynamodb(wa_id, message_id, body, msg_type):
    save_message(wa_id, message_id, body, msg_type)


@app.task(name="store_thread_to_dynamodb")
def store_thread_to_dynamodb(wa_id: str, thread_id: str):
    try:
        logging.info("[Celery] Saving thread to DynamoDB for %s", wa_id)
        save_thread(wa_id, thread_id)
        logging.info("[Celery] Thread saved for %s", wa_id)
    except Exception as e:
        logging.error("[Celery] Failed to save thread for %s: %s", wa_id, e)


@app.task(name="tasks.handle_document_upload_async")
def handle_document_upload_async(wa_id, media_id, filename, thread_id=None):
    try:
        logging.info("[Celery] Start upload for %s file=%s", wa_id, filename)
        file_bytes, _, content_type = download_whatsapp_media(media_id, filename)
        s3_url = save_file_to_s3(file_bytes, filename, content_type)
        logging.info("[Celery] Uploaded to S3: %s", s3_url)

        if thread_id:
            save_thread(wa_id, thread_id)

        return {"ok": True, "s3_url": s3_url}
    except Exception as e:
        logging.exception("[Celery] Document upload failed for %s", wa_id)
        return {"ok": False, "error": str(e)}
