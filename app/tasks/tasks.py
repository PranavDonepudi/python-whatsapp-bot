# --- app/tasks/tasks.py ---
from app.celery_app import celery_app
from app.services.whatsapp_service import save_file_to_s3
from app.services.dynamodb import save_thread


@celery_app.task(name="app.tasks.save_resume_file_async")
def save_resume_file_async(file_bytes, filename, content_type):
    try:
        save_file_to_s3(file_bytes, filename, content_type)
    except Exception as e:
        print(f"Failed to upload file to S3: {e}")


@celery_app.task(name="app.tasks.update_thread_info_async")
def update_thread_info_async(wa_id, thread_id):
    try:
        save_thread(wa_id, thread_id)
    except Exception as e:
        print(f"Failed to update thread in DB: {e}")
