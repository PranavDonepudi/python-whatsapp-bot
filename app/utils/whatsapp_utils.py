import os
import json
import logging
import re
import uuid
from datetime import datetime

import boto3
import requests

# ————————
# Configuration via environment variables
# ————————
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERSION = os.getenv("VERSION", "v18.0")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
RESUME_BUCKET = os.getenv("RESUME_BUCKET")


def log_http_response(response: requests.Response) -> None:
    """
    Log status code, content type, and body of an HTTP response.
    """
    logging.info("Status: %s", response.status_code)
    logging.info("Content-type: %s", response.headers.get("content-type"))
    logging.info("Body: %s", response.text)


def get_text_message_input(recipient: str, text: str) -> dict:
    """
    Construct the payload for sending a WhatsApp text message.
    """
    return {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }


def send_message(payload: dict) -> requests.Response:
    """
    Send a WhatsApp message via the Meta Graph API.
    `payload` should be a dict; this function will JSON-encode it.
    """
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("WhatsApp ACCESS_TOKEN or PHONE_NUMBER_ID is not set")

    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ACCESS_TOKEN}",
    }
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    log_http_response(response)
    return response


def _get_s3_client():
    """
    Create a boto3 S3 client using environment credentials.
    """
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        raise RuntimeError("AWS credentials are not set in environment")

    return boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    )


def download_whatsapp_media(media_id: str, filename: str = None):
    """
    1) Fetch the media URL from Graph API metadata.
    2) Download the bytes and return (bytes, filename, content_type).
    """
    meta_url = f"https://graph.facebook.com/{VERSION}/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

    meta_res = requests.get(meta_url, headers=headers, timeout=5)
    meta_res.raise_for_status()
    media_url = meta_res.json().get("url")

    media_res = requests.get(media_url, headers=headers, timeout=10)
    media_res.raise_for_status()

    content_type = media_res.headers.get("Content-Type")
    file_bytes = media_res.content

    if not filename:
        ext = content_type.split("/")[-1]
        filename = f"{media_id}.{ext}"

    return file_bytes, filename, content_type


def save_file_to_s3(file_bytes: bytes, filename: str, content_type: str) -> str:
    """
    Upload bytes to S3 under RESUME_BUCKET, return public URL.
    """
    if not RESUME_BUCKET:
        raise RuntimeError("RESUME_BUCKET is not set in environment")

    client = _get_s3_client()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    key = f"resumes/{timestamp}_{filename}"

    client.put_object(
        Bucket=RESUME_BUCKET,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    logging.info("Uploaded to S3 key: %s/%s", RESUME_BUCKET, key)
    return f"https://{RESUME_BUCKET}.s3.amazonaws.com/{key}"


def process_text_for_whatsapp(text: str) -> str:
    """
    Strip bracketed annotations and convert **bold** to *italic* for WhatsApp.
    """
    text = re.sub(r"\【.*?\】", "", text).strip()
    return re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)


def process_whatsapp_message(body: dict):
    """
    Dispatch incoming webhook payload. Returns a Response for sync flows or None.
    """
    value = body["entry"][0]["changes"][0]["value"]
    wa_id = value["contacts"][0]["wa_id"]
    name = value["contacts"][0]["profile"]["name"]
    message = value["messages"][0]
    msg_type = message["type"]

    from app.services.openai_service import check_if_thread_exists, client, store_thread
    from app.tasks.tasks import process_whatsapp_text_async

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


def is_valid_whatsapp_message(body: dict) -> bool:
    """
    Check if payload contains a valid WhatsApp message structure.
    """
    try:
        value = body["entry"][0]["changes"][0]["value"]
        return bool(value.get("messages"))
    except (IndexError, KeyError):
        return False
