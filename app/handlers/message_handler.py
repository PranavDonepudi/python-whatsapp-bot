import uuid
import logging
from app.services.dynamodb import is_duplicate_message, mark_message_as_processed
from app.services.openai_service import (
    check_if_thread_exists,
    store_thread,
    run_assistant_and_get_response,
)
from app.services.whatsapp_service import (
    download_whatsapp_media,
    send_message,
    get_text_message_input,
    process_text_for_whatsapp,
)
from app.tasks.tasks import save_resume_file_async, update_thread_info_async


def is_valid_whatsapp_message(body):
    try:
        value = body["entry"][0]["changes"][0]["value"]
        return "messages" in value and "contacts" in value
    except (KeyError, IndexError):
        return False


def extract_whatsapp_message(body):
    value = body["entry"][0]["changes"][0]["value"]
    wa_id = value["contacts"][0]["wa_id"]
    name = value["contacts"][0]["profile"]["name"]
    message = value["messages"][0]
    return wa_id, name, message


def initialize_thread_if_needed(wa_id):
    thread_id = check_if_thread_exists(wa_id)
    if not thread_id:
        logging.info("Creating new thread for wa_id %s", wa_id)
        from openai import OpenAI

        client = OpenAI()
        thread = client.beta.threads.create()
        thread_id = thread.id
        store_thread(wa_id, thread_id)
    return thread_id


def handle_document_message(wa_id, name, message, thread_id):
    media_id = message["document"]["id"]
    filename = message["document"].get("filename", f"{wa_id}_{uuid.uuid4()}.pdf")
    file_bytes, _, content_type = download_whatsapp_media(media_id)

    send_message(
        get_text_message_input(wa_id, "Thanks! We've received your document...")
    )

    reply = run_assistant_and_get_response(wa_id, name, None)
    if reply:
        send_message(get_text_message_input(wa_id, process_text_for_whatsapp(reply)))

    save_resume_file_async.delay(file_bytes, filename, content_type)
    update_thread_info_async.delay(wa_id, thread_id)


def handle_text_message(wa_id, name, message_text):
    reply = run_assistant_and_get_response(wa_id, name, message_text)
    if reply:
        send_message(get_text_message_input(wa_id, process_text_for_whatsapp(reply)))
    else:
        send_message(
            get_text_message_input(
                wa_id, "Sorry, I couldn't process that right now. Please try again."
            )
        )


def handle_status_event(body):
    value = body["entry"][0]["changes"][0]["value"]
    statuses = value.get("statuses", [])
    logging.info("Received status event: %s", statuses)


def handle_whatsapp_event(body):
    try:
        if "statuses" in body["entry"][0]["changes"][0]["value"]:
            handle_status_event(body)
            return

        if not is_valid_whatsapp_message(body):
            logging.info("Skipping non-message webhook payload")
            return

        wa_id, name, message = extract_whatsapp_message(body)
        message_id = message["id"]
        msg_type = message["type"]

        if is_duplicate_message(message_id):
            logging.info("Duplicate message %s ignored.", message_id)
            return

        mark_message_as_processed(message_id)
        thread_id = initialize_thread_if_needed(wa_id)

        if msg_type == "document":
            handle_document_message(wa_id, name, message, thread_id)
        elif msg_type == "text":
            handle_text_message(wa_id, name, message["text"]["body"])
        else:
            logging.warning("Unhandled message type: %s", msg_type)

    except Exception as e:
        logging.exception("Failed to handle WhatsApp event: %s", str(e))
