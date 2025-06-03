import logging
from flask import current_app, jsonify
import json
import requests
from app.services.openai_service import check_if_thread_exists, handle_candidate_reply
from app.services.openai_service import store_thread  # Import store_thread
import re
import os
from datetime import datetime

# Import or initialize the OpenAI client
from app.services.openai_service import client


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


# --- WhatsApp Media Download Helper ---
def download_whatsapp_media(media_id):
    headers = {
        "Authorization": f"Bearer {current_app.config['ACCESS_TOKEN']}",
    }

    # Step 1: Get media URL
    meta_url = f"https://graph.facebook.com/v18.0/{media_id}"
    meta_res = requests.get(meta_url, headers=headers)
    media_url = meta_res.json().get("url")

    # Step 2: Download media
    media_res = requests.get(media_url, headers=headers)
    return media_res.content, media_res.headers.get("Content-Type")


# Helper to save file locally
def save_file_locally(file_bytes, filename):
    base_path = os.path.join(os.getcwd(), "resumes")
    if not os.path.exists(base_path):
        os.makedirs(base_path)

    # Create unique filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_filename = f"{timestamp}_{filename}"
    full_path = os.path.join(base_path, safe_filename)

    with open(full_path, "wb") as f:
        f.write(file_bytes)

    logging.info(f"Saved file locally at: {full_path}")
    return full_path


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

    # --- Handle Document Upload ---
    if msg_type == "document":
        media_id = message["document"]["id"]
        filename = message["document"].get("filename", f"{wa_id}_{uuid.uuid4()}.pdf")

        file_bytes, content_type = download_whatsapp_media(media_id)

        # Validate file type (allow only PDFs and DOCs)
        if content_type not in [
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ]:
            reply = "Only PDF or Word document resumes are accepted. Please upload a valid file."
        else:
            save_file_locally(file_bytes, filename)
            reply = f"Thanks {name}, we’ve successfully received your resume!"

        formatted_msg = process_text_for_whatsapp(reply)

    # --- Handle Text Message ---
    elif msg_type == "text":
        message_body = message["text"]["body"]
        if not thread_id:
            thread = client.beta.threads.create()
            store_thread(wa_id, thread.id)
            default_msg = (
                f"Hi {name}, Congratulations! 🎉 You have been selected for a role at TechnoGen. "
                "Reply *yes* if you're interested or *update* if you'd like to send a new resume."
            )
            formatted_msg = process_text_for_whatsapp(default_msg)
        else:
            formatted_msg = process_text_for_whatsapp(
                handle_candidate_reply(message_body, wa_id, name)
            )

    else:
        formatted_msg = (
            f"Sorry {name}, we only support text and resume file messages at this time."
        )
        formatted_msg = process_text_for_whatsapp(formatted_msg)

    data = get_text_message_input(wa_id, formatted_msg)
    send_message(data)


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
