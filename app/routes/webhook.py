# --- app/routes/webhook.py ---
import logging
from flask import Blueprint, request, jsonify, current_app

from app.utils.responses import respond_error
from app.decorators.security import signature_required
from app.services.sqs import push_message_to_sqs
from app.handlers.message_handler import (
    is_valid_whatsapp_message,
    extract_whatsapp_message,
)

webhook_blueprint = Blueprint("webhook", __name__)


@webhook_blueprint.route("/webhook", methods=["GET"])
def webhook_get():
    token = request.args.get("hub.verify_token")
    mode = request.args.get("hub.mode")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == current_app.config["VERIFY_TOKEN"]:
        logging.info("WEBHOOK_VERIFIED")
        return challenge, 200
    logging.warning("WEBHOOK_VERIFICATION_FAILED")
    return respond_error("Verification failed", 403)


@webhook_blueprint.route("/webhook", methods=["POST"])
@signature_required
def webhook_post():
    try:
        body = request.get_json()
        if is_valid_whatsapp_message(body):
            wa_id, name, message = extract_whatsapp_message(body)
            push_message_to_sqs(
                {
                    "wa_id": wa_id,
                    "name": name,
                    "message_type": message.get("type"),
                    "message_body": message.get("text", {}).get("body"),
                    "media_id": message.get("document", {}).get("id"),
                    "filename": message.get("document", {}).get("filename"),
                    "message_id": message.get("id"),
                }
            )
        statuses = (
            body.get("entry", [])[0]
            .get("changes", [])[0]
            .get("value", {})
            .get("statuses", [])
        )
        for status in statuses:
            message_id = status.get("id")
            status_type = status.get("status")
            errors = status.get("errors", [])
            wa_id = status.get("recipient_id")

            logging.info(
                f"Status update: {status_type} for {wa_id} (msg_id={message_id})"
            )

            if status_type == "failed":
                for error in errors:
                    logging.error(
                        f"Message failed for {wa_id}: {error.get('code')} - {error.get('title')}"
                    )

        return jsonify({"status": "processed"}), 200
    except Exception as e:
        logging.exception("Webhook failed")
        return jsonify({"error": str(e)}), 500
