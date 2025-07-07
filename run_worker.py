# run_worker.py
import boto3
import json
import logging
import os
import time
from botocore.exceptions import ClientError
from app.handlers.message_handler import (
    download_whatsapp_media,
    send_message,
    get_text_message_input,
    process_text_for_whatsapp,
    initialize_thread_if_needed,
)
from app.tasks.tasks import save_resume_file_async, update_thread_info_async
from app.services.openai_service import run_assistant_and_get_response
from app.services.dynamodb import (
    is_duplicate_message,
    mark_message_as_processed,
)

logging.basicConfig(level=logging.INFO)

# Configure SQS client
sqs = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-2"))
QUEUE_URL = os.getenv("SQS_QUEUE_URL")


def process_sqs_message(message: dict):
    wa_id = message["wa_id"]
    name = message["name"]
    msg_type = message["message_type"]
    msg_body = message.get("message_body")
    media_id = message.get("media_id")
    filename = message.get("filename")
    message_id = message.get("message_id")

    if is_duplicate_message(message_id):
        return
    mark_message_as_processed(message_id)
    thread_id = initialize_thread_if_needed(wa_id)

    if msg_type == "text":
        reply = run_assistant_and_get_response(wa_id, name, msg_body)
        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )
    elif msg_type == "document":
        file_bytes, _, content_type = download_whatsapp_media(media_id)
        send_message(
            get_text_message_input(wa_id, "Thanks! We've received your document.")
        )
        reply = run_assistant_and_get_response(wa_id, name, None)
        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )
        save_resume_file_async.delay(file_bytes, filename, content_type)
        update_thread_info_async.delay(wa_id, thread_id)


def poll_sqs():
    logging.info("[Worker] Starting polling loop...")

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=10,  # Long-polling to reduce cost
                VisibilityTimeout=60,  # Allow 60 seconds to process before retry
            )

            messages = response.get("Messages", [])
            if not messages:
                continue

            for msg in messages:
                receipt_handle = msg["ReceiptHandle"]
                try:
                    body = json.loads(msg["Body"])
                    logging.info(f"[Worker] Received message: {body}")

                    process_sqs_message(body)

                    # If successful, delete message from queue
                    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)
                    logging.info("[Worker] Deleted message from queue.")

                except Exception as e:
                    logging.exception(f"[Worker] Failed to process message: {e}")

        except ClientError as e:
            logging.error(f"[Worker] AWS ClientError: {e}")
            time.sleep(5)  # Backoff before retry


if __name__ == "__main__":
    poll_sqs()
