# --- app/services/whatsapp_service.py ---
import os
import logging
import re
import requests
import boto3
import httpx
from datetime import datetime

WHATSAPP_API_URL = (
    f"https://graph.facebook.com/v18.0/{os.getenv('PHONE_NUMBER_ID')}/messages"
)
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERSION = os.getenv("VERSION", "v18.0")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
RESUME_BUCKET = os.getenv("RESUME_BUCKET")


def log_http_response(response: requests.Response) -> None:
    logging.info("Status: %s", response.status_code)
    logging.info("Content-type: %s", response.headers.get("content-type"))
    logging.info("Body: %s", response.text)


def get_text_message_input(recipient: str, text: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }


def send_message(payload: dict) -> requests.Response:
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
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "us-east-2"),  # use your region here
    )


def download_whatsapp_media(media_id: str, filename: str = None):
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
    if not RESUME_BUCKET:
        raise RuntimeError("RESUME_BUCKET is not set in environment")

    client = _get_s3_client()
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    key = f"raw/{timestamp}_{_safe_name(filename)}"
    client.put_object(
        Bucket=RESUME_BUCKET,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    logging.info("Uploaded to S3 key: %s/%s", RESUME_BUCKET, key)
    return f"https://{RESUME_BUCKET}.s3.amazonaws.com/{key}"


_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(name: str) -> str:
    # Drop any path parts and replace odd chars with underscores
    base = os.path.basename(name)
    return _SAFE_CHARS.sub("_", base)


def process_text_for_whatsapp(text: str) -> str:
    text = re.sub(r"\【.*?\】", "", text).strip()
    return re.sub(r"\*\*(.*?)\*\*", r"*\\1*", text)
