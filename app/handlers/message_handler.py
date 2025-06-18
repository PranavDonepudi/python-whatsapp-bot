# --- app/handlers/message_handler.py ---
import uuid
import logging

from app.services.openai_service import (
    check_if_thread_exists,
    store_thread,
    run_assistant_and_get_response,
)
from app.services.whatsapp_service import (
    get_text_message_input,
    download_whatsapp_media,
    send_message,
    process_text_for_whatsapp,
)
from app.tasks.tasks import save_resume_file_async, update_thread_info_async
from app.services.dynamodb import is_duplicate_message, mark_message_as_processed


def handle_whatsapp_event(body):
    value = body["entry"][0]["changes"][0]["value"]
    wa_id = value["contacts"][0]["wa_id"]
    name = value["contacts"][0]["profile"]["name"]
    message = value["messages"][0]
    message_id = message["id"]
    msg_type = message["type"]

    if is_duplicate_message(message_id):
        logging.info("Duplicate message %s ignored.", message_id)
        return None
    mark_message_as_processed(message_id)

    thread_id = check_if_thread_exists(wa_id)
    if not thread_id:
        logging.info("Creating new thread for wa_id %s", wa_id)
        from openai import OpenAI

        client = OpenAI()
        thread = client.beta.threads.create()
        thread_id = thread.id
        store_thread(wa_id, thread_id)

    if msg_type == "document":
        media_id = message["document"]["id"]
        filename = message["document"].get("filename", f"{wa_id}_{uuid.uuid4()}.pdf")
        file_bytes, _, content_type = download_whatsapp_media(media_id)

        send_message(
            get_text_message_input(wa_id, "Thanks! We've received your document...")
        )

        # Trigger assistant with context (no message added, but useful)
        reply = run_assistant_and_get_response(wa_id, name, None)
        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )

        # Offload heavy tasks
        save_resume_file_async.delay(file_bytes, filename, content_type)
        update_thread_info_async.delay(wa_id, thread_id)

    elif msg_type == "text":
        user_message = message["text"]["body"]
        reply = run_assistant_and_get_response(wa_id, name, user_message)
        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )
        else:
            send_message(
                get_text_message_input(
                    wa_id, "Sorry, I couldn't process that right now. Please try again."
                )
            )
