# --- app/tasks/gpt_reply_worker.py ---
import json
import time
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

from app.services.openai_service import generate_response

client = OpenAI()


def handle_gpt_reply(payload):
    wa_id = payload["wa_id"]
    name = payload.get("name", "Candidate")
    media_id = payload.get("media_id")
    filename = payload.get("filename") or f"{wa_id}_{uuid.uuid4()}.pdf"
    message_type = payload.get("message_type", "text")
    message_body = payload.get("message_body", "")

    logging.info(f"[GPT Worker] Handling message from {wa_id}: {message_body}")

    try:
        # Handle document uploads first
        if message_type == "document":
            send_message(
                get_text_message_input(wa_id, "Thanks! We've received your resume.")
            )
            file_bytes, _, content_type = download_whatsapp_media(media_id, filename)
            save_file_to_s3(file_bytes, filename, content_type)
            return

        # For text messages, generate response using GPT
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
