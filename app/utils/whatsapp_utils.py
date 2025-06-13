import json
import logging
import re
import uuid
from datetime import datetime

import boto3
import requests
from flask import current_app, jsonify
from app.tasks.tasks import process_whatsapp_text_async

# Import or initialize the OpenAI client
from app.services.openai_service import store_thread  # Import store_thread
from app.services.openai_service import (
    check_if_thread_exists,
    client,
    handle_candidate_reply,
)


def log_http_response(response):
    logging.info("Status: %s", response.status_code)
    logging.info("Content-type: %s", response.headers.get("content-type"))
    logging.info("Body: %s", response.text)


def get_text_message_input(recipient, text):
    return json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
    )


def send_message(data):
    headers = {
        "Content-type": "application/json",
        "Authorization": f"Bearer {current_app.config['ACCESS_TOKEN']}",
    }

    url = f"https://graph.facebook.com/{current_app.config['VERSION']}/{current_app.config['PHONE_NUMBER_ID']}/messages"

    try:
        response = requests.post(
            url, data=data, headers=headers, timeout=10
        )  # 10 seconds timeout as an example
        response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code
    except requests.Timeout:
        logging.error("Timeout occurred while sending message")
        return jsonify({"status": "error", "message": "Request timed out"}), 408
    except (
        requests.RequestException
    ) as e:  # This will catch any general request exception
        logging.error("Request failed due to: %s", e)
        return jsonify({"status": "error", "message": "Failed to send message"}), 500
    else:
        # Process the response as normal
        log_http_response(response)
        return response


def _get_s3_client():
    """Create an S3 client _inside_ an app context."""
    return boto3.client(
        "s3",
        aws_access_key_id=current_app.config["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=current_app.config["AWS_SECRET_ACCESS_KEY"],
        region_name=current_app.config.get("AWS_REGION", "us-east-1"),
    )


# --- WhatsApp Media Download Helper ---
def download_whatsapp_media(media_id, filename=None):
    headers = {
        "Authorization": f"Bearer {current_app.config['ACCESS_TOKEN']}",
    }
    # 1) fetch the media URL
    meta_url = f"https://graph.facebook.com/v18.0/{media_id}"
    meta_res = requests.get(meta_url, headers=headers)
    media_url = meta_res.json().get("url")

    # 2) download the bytes
    media_res = requests.get(media_url, headers=headers)
    content_type = media_res.headers.get("Content-Type")
    file_bytes = media_res.content

    if not filename:
        ext = content_type.split("/")[-1]
        filename = f"{media_id}.{ext}"

    return file_bytes, filename, content_type


# Helper to save file locally
def save_file_to_s3(file_bytes, filename, content_type):
    # 1) Read from app.config
    aws_key = current_app.config.get("AWS_ACCESS_KEY_ID")
    aws_secret = current_app.config.get("AWS_SECRET_ACCESS_KEY")
    aws_region = current_app.config.get("AWS_REGION")
    bucket = current_app.config.get("RESUME_BUCKET")

    # 2) Log out what you got
    logging.info("save_file_to_s3(): AWS_ACCESS_KEY_ID=%r", aws_key)
    logging.info(
        "save_file_to_s3(): AWS_SECRET_ACCESS_KEY present? %s", bool(aws_secret)
    )
    logging.info("save_file_to_s3(): AWS_REGION=%r", aws_region)
    logging.info("save_file_to_s3(): RESUME_BUCKET=%r", bucket)
    # 3) Create the client and upload
    s3 = boto3.client(
        "s3",
        aws_access_key_id=aws_key,
        aws_secret_access_key=aws_secret,
        region_name=aws_region,
    )
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    key = f"resumes/{timestamp}_{filename}"
    s3.put_object(Bucket=bucket, Key=key, Body=file_bytes, ContentType=content_type)
    logging.info("Uploaded to S3 key: %s/%s", bucket, key)

    return f"https://{bucket}.s3.amazonaws.com/{key}"


def process_text_for_whatsapp(text):
    # Remove brackets
    pattern = r"\【.*?\】"
    # Substitute the pattern with an empty string
    text = re.sub(pattern, "", text).strip()

    # Pattern to find double asterisks including the word(s) in between
    pattern = r"\*\*(.*?)\*\*"

    # Replacement pattern with single asterisks
    replacement = r"*\1*"

    # Substitute occurrences of the pattern with the replacement
    whatsapp_style_text = re.sub(pattern, replacement, text)

    return whatsapp_style_text


def process_whatsapp_message(body):
    value = body["entry"][0]["changes"][0]["value"]
    wa_id = value["contacts"][0]["wa_id"]
    name = value["contacts"][0]["profile"]["name"]
    message = value["messages"][0]
    msg_type = message["type"]

    # Check if this is a new candidate or first message
    thread_id = check_if_thread_exists(wa_id)

    # --- 1) Document Uploads stay synchronous ---
    if msg_type == "document":
        media_id = message["document"]["id"]
        filename = message["document"].get("filename", f"{wa_id}_{uuid.uuid4()}.pdf")

        file_bytes, _, content_type = download_whatsapp_media(media_id, filename)

        if content_type not in [
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ]:
            reply = "Only PDF or Word document resumes are accepted. Please upload a valid file."
        else:
            s3_url = save_file_to_s3(file_bytes, filename, content_type)
            reply = f"Thanks {name}, we’ve successfully received your resume!"

        formatted_msg = process_text_for_whatsapp(reply)
        data = get_text_message_input(wa_id, formatted_msg)
        return send_message(data)

    # --- 2) Text messages go through Celery ---
    elif msg_type == "text":
        text_body = message["text"]["body"]

        # 2a) First‐time greeting
        if not thread_id:
            # create & store thread
            thread = client.beta.threads.create()
            store_thread(wa_id, thread.id)

            default_msg = (
                f"Hi {name}, this is WhatsApp bot assistant for TechnoGen. "
                "I'm here to assist you with any questions you may have about our job openings. "
                "Feel free to ask me anything related to our job opportunities or the application process."
            )
            welcome = process_text_for_whatsapp(default_msg)
            send_message(get_text_message_input(wa_id, welcome))

        # 2b) Always enqueue a background task to handle candidate reply
        process_whatsapp_text_async.delay(wa_id, name, text_body)
        # return early since the async task will send the actual response
        return

    # --- 3) Unsupported media types ---
    else:
        reply = (
            f"Sorry {name}, we only support text and resume file messages at this time."
        )
        formatted_msg = process_text_for_whatsapp(reply)
        data = get_text_message_input(wa_id, formatted_msg)
        return send_message(data)


def is_valid_whatsapp_message(body):
    """
    Check if the incoming webhook event has a valid WhatsApp message structure.
    """
    return (
        body.get("object")
        and body.get("entry")
        and body["entry"][0].get("changes")
        and body["entry"][0]["changes"][0].get("value")
        and body["entry"][0]["changes"][0]["value"].get("messages")
        and body["entry"][0]["changes"][0]["value"]["messages"][0]
    )
