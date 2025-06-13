from app.celery_app import celery_app
from app.services.openai_service import generate_response
from app.utils.whatsapp_utils import send_message, get_text_message_input
import logging


@celery_app.task
def process_whatsapp_text_async(wa_id, name, message_body):
    logging.info(f"Starting async processing for {wa_id}")
    response = generate_response(message_body, wa_id, name)
    data = get_text_message_input(wa_id, response)
    send_message(data)
    logging.info(f"Completed async task for {wa_id}")
