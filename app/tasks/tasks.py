# app/tasks/tasks.py

import logging
from app.celery_app import celery_app
from app.services.openai_service import generate_response
from app.services.whatsapp_service import send_message, get_text_message_input


@celery_app.task
def process_whatsapp_text_async(wa_id: str, name: str, message_body: str):
    """
    Background task to process WhatsApp text using OpenAI and respond.
    """
    logging.info("Starting async processing for %s", wa_id)
    try:
        response = generate_response(message_body, wa_id, name)
        data = get_text_message_input(wa_id, response)
        send_message(data)
        logging.info("Completed async task for %s", wa_id)
    except Exception as e:
        logging.error("Async processing failed for %s: %s", wa_id, str(e))
