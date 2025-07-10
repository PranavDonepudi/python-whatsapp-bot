# --- app/tasks/gpt_reply_worker.py ---
import logging
import uuid
from celery_app import app
from openai import OpenAI
from app.services.dynamodb import save_thread
from app.services.whatsapp_service import (
    send_message,
    get_text_message_input,
    process_text_for_whatsapp,
)
from app.tasks.background_tasks import (
    handle_document_upload_async,
)
from app.services.openai_service import check_if_thread_exists, generate_response

client = OpenAI()


def handle_gpt_reply(payload):
    wa_id = payload["wa_id"]
    name = payload.get("name", "Candidate")
    media_id = payload.get("media_id")
    filename = payload.get("filename") or f"{wa_id}_{uuid.uuid4()}.pdf"
    message_type = payload.get("message_type", "text")
    message_body = payload.get("message_body", "")

    logging.info("[GPT Worker] Handling message from %s: %s", wa_id, message_body)

    try:
        # Step 1: Check or create thread
        thread_id = check_if_thread_exists(wa_id)
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_thread(wa_id, thread_id)

        # Step 2: Handle document uploads separately
        if message_type == "document":
            send_message(
                get_text_message_input(wa_id, "Thanks! We've received your resume.")
            )  # Push to background
            handle_document_upload_async.delay(wa_id, media_id, filename, thread_id)
            return
        # Skip unsupported or empty messages
        if not message_body or message_type not in ["text", "document"]:
            logging.warning(
                "[GPT Worker] Skipping unsupported or empty message from %s", wa_id
            )
            return
        # Step 3: Generate GPT response using context
        try:
            reply = generate_response(message_body, wa_id, name)
        except Exception as gpt_error:
            logging.exception("[GPT Worker] GPT failed for %s: %s", wa_id, gpt_error)
            fallback = "Sorry, we're facing a temporary issue. Please try again in a few minutes."
            send_message(get_text_message_input(wa_id, fallback))
            return

        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )
            logging.info("[GPT Worker] Replied to %s", wa_id)
        else:
            logging.warning(f"[GPT Worker] No assistant reply for {wa_id}")

    except Exception as e:
        logging.exception(f"[GPT Worker] Failed to process message for {wa_id}: {e}")
