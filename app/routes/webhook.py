# --- app/routes/webhook.py ---
import logging
import json
from flask import Blueprint, request, jsonify, current_app
from app.handlers.message_handler import handle_whatsapp_event
from app.utils.validators import is_valid_whatsapp_message
from app.utils.responses import respond_ok, respond_error
from app.decorators.security import signature_required

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
    body = request.get_json()

    if (
        body.get("entry", [{}])[0]
        .get("changes", [{}])[0]
        .get("value", {})
        .get("statuses")
    ):
        logging.info("Received a WhatsApp status update.")
        return respond_ok()

    if is_valid_whatsapp_message(body):
        return handle_whatsapp_event(body)

    return respond_error("Not a WhatsApp API event", 404)
