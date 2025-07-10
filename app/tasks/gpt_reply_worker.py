# --- app/tasks/gpt_reply_worker.py ---
import logging
import uuid

from openai import OpenAI
from app.services.whatsapp_service import (
    send_message,
    get_text_message_input,
    process_text_for_whatsapp,
    download_whatsapp_media,
    save_file_to_s3,
)

from app.services.openai_service import (
    check_if_thread_exists,
    generate_response,
    save_thread,
)

client = OpenAI()


def handle_gpt_reply(payload):
    wa_id = payload["wa_id"]
    name = payload.get("name", "Candidate")
    media_id = payload.get("media_id")
    filename = payload.get("filename") or f"{wa_id}_{uuid.uuid4()}.pdf"
    message_type = payload.get("message_type", "text")
    message_body = payload.get("message_body", "")

    logging.info(f"[GPT Worker] Handling message from {wa_id}: {message_body}")

    # Skip unsupported or empty messages
    if not message_body or message_type not in ["text", "document"]:
        logging.warning(
            f"[GPT Worker] Skipping unsupported or empty message from {wa_id}"
        )
        return

    try:
        # Step 1: Check or create thread
        thread_id = check_if_thread_exists(wa_id)
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_thread(wa_id, thread_id)

        # Step 2: Handle document upload separately
        if message_type == "document":
            send_message(
                get_text_message_input(wa_id, "Thanks! We've received your resume.")
            )
            file_bytes, _, content_type = download_whatsapp_media(media_id, filename)
            save_file_to_s3(file_bytes, filename, content_type)
            return

        # Step 3: Generate GPT response using context-aware function
        reply = generate_response(message_body, wa_id, name)

        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )
            logging.info(f"[GPT Worker] Replied to {wa_id}")
        else:
            logging.warning(f"[GPT Worker] No assistant reply for {wa_id}")

    except Exception as e:
        logging.exception(f"[GPT Worker] Failed to process message for {wa_id}: {e}")
