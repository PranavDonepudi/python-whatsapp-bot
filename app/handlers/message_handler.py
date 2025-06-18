# --- app/handlers/message_handler.py ---
import uuid
import logging
from app.services.openai_service import check_if_thread_exists, store_thread
from app.services.whatsapp_service import (
    download_whatsapp_media,
    send_message,
    get_text_message_input,
    process_text_for_whatsapp,
)
from app.tasks.tasks import process_whatsapp_text_async


def handle_whatsapp_event(body):
    value = body["entry"][0]["changes"][0]["value"]
    wa_id = value["contacts"][0]["wa_id"]
    name = value["contacts"][0]["profile"]["name"]
    message = value["messages"][0]
    message_id = message["id"]
    msg_type = message["type"]

    from app.services.dynamodb import is_duplicate_message, mark_message_as_processed
    from app.services.openai_service import check_if_thread_exists, store_thread, client

    if is_duplicate_message(message_id):
        logging.info("Duplicate message %s ignored.", message_id)
        return None
    mark_message_as_processed(message_id)

    thread_id = check_if_thread_exists(wa_id)

    if msg_type == "document":
        media_id = message["document"]["id"]
        filename = message["document"].get("filename", f"{wa_id}_{uuid.uuid4()}.pdf")

        file_bytes, _, content_type = download_whatsapp_media(media_id, filename)
        if content_type not in (
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ):
            reply = "Only PDF or Word documents accepted. Please upload your resume."
        else:
            reply = f"Thanks {name}, we got your resume updated. I'm happy to help you further on any questions."

        data = get_text_message_input(wa_id, process_text_for_whatsapp(reply))
        return send_message(data)

    if msg_type == "text":
        text_body = message["text"]["body"]

        if not thread_id:
            thread = client.beta.threads.create()
            store_thread(wa_id, thread.id)

            welcome = (
                f"Hi {name}, welcome to TechnoGen's job bot! "
                "If you want to update your resume, please upload it as a document. "
                "Ask me anything about our openings or application process."
            )
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(welcome))
            )

        process_whatsapp_text_async.delay(wa_id, name, text_body)
        return None

    fallback = (
        f"Sorry {name}, I only handle text messages and resume uploads right now."
    )
    return send_message(
        get_text_message_input(wa_id, process_text_for_whatsapp(fallback))
    )
