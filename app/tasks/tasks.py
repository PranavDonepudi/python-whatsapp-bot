# --- app/tasks/tasks.py ---
from app.celery_app import celery_app
from app.services.whatsapp_service import save_file_to_s3, process_text_for_whatsapp
from app.services.dynamodb import save_thread
from app.services.openai_service import (
    check_if_thread_exists,
    store_thread,
    is_active_run,
    safe_add_message_to_thread,
)
from app.services.whatsapp_service import (
    send_message,
    get_text_message_input,
)  # Add this import

from openai import OpenAI
import time
import logging
import os

client = OpenAI()


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


@celery_app.task
def process_whatsapp_text_async(wa_id: str, name: str, message_body: str):
    logging.warning(f"Processing WhatsApp message: {wa_id}, {name}, {message_body}")
    try:
        # Step 1: Get or create thread
        thread_id = check_if_thread_exists(wa_id)
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            store_thread(wa_id, thread_id)

        # Step 2: Add user message to thread
        safe_add_message_to_thread(thread_id, message_body)

        # Step 3: Skip if run is already in progress
        if is_active_run(thread_id):
            logging.info(f"Active run already in progress for {wa_id}")
            return

        # Step 4: Run assistant
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=os.getenv("OPENAI_ASSISTANT_ID"),
            instructions=f"You are talking to {name}, a job candidate. Be warm and professional.",
        )

        # Step 5: Poll for result
        for _ in range(20):
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread_id, run_id=run.id
            ).status
            if run_status == "completed":
                break
            elif run_status in ("failed", "cancelled", "expired"):
                logging.warning(f"Run failed or cancelled for {wa_id}")
                return
            time.sleep(1)

        # Step 6: Fetch latest assistant reply
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in reversed(messages.data):
            if msg.role == "assistant":
                reply_text = msg.content[0].text.value
                cleaned = process_text_for_whatsapp(reply_text)
                send_message(get_text_message_input(wa_id, cleaned))
                logging.info(f"Assistant reply sent to {wa_id}")
                return

        logging.warning(f"No assistant message found for {wa_id}")

    except Exception as e:
        logging.exception(f"Error processing message for {wa_id}: {e}")
