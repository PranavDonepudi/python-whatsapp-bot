# --- app/tasks/gpt_reply_worker.py ---
import json
import time
import logging
import os
import uuid

from openai import OpenAI
from app.services.whatsapp_service import (
    send_message,
    download_whatsapp_media,
    save_file_to_s3,
    get_text_message_input,
    process_text_for_whatsapp,
)

from app.services.openai_service import (
    check_if_thread_exists,
    safe_add_message_to_thread,
    is_active_run,
    run_assistant,
)
from app.services.dynamodb import save_thread

client = OpenAI()


def handle_gpt_reply(payload):
    wa_id = payload["wa_id"]
    name = payload.get("name", "Candidate")
    message_type = payload.get("message_type", "text")
    message_body = payload.get("message_body", "")
    media_id = payload.get("media_id")
    filename = payload.get("filename") or f"{wa_id}_{uuid.uuid4()}.pdf"

    logging.info(f"[GPT Worker] Handling message from {wa_id}: {message_body}")

    try:
        # Step 1: Check or create thread
        thread_id = check_if_thread_exists(wa_id)
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_thread(wa_id, thread_id)

        # Step 2: If document, handle upload and exit
        if message_type == "document":
            file_bytes, _, content_type = download_whatsapp_media(media_id, filename)

            send_message(
                get_text_message_input(wa_id, "Thanks! We've received your resume.")
            )
            save_file_to_s3(file_bytes, filename, content_type)
            save_thread(wa_id, thread_id)
            return

        # Step 3: If text, process via assistant
        safe_add_message_to_thread(thread_id, message_body)

        if is_active_run(thread_id):
            logging.warning(f"[GPT Worker] Run already active for {wa_id}")
            return

        reply = run_assistant(thread_id, name)
        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )
            logging.info(f"[GPT Worker] Replied to {wa_id}")
        else:
            logging.warning(f"[GPT Worker] No assistant reply for {wa_id}")

    except Exception as e:
        logging.exception(f"[GPT Worker] Failed to process message for {wa_id}: {e}")
