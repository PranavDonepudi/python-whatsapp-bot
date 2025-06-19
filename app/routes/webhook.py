# --- app/routes/webhook.py ---
import logging
from flask import Blueprint, request, jsonify, current_app
from app.handlers.message_handler import handle_whatsapp_event

from app.utils.responses import respond_error
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
    try:
        body = request.get_json()
        handle_whatsapp_event(body)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.exception("Webhook failed")
        return jsonify({"error": str(e)}), 500
