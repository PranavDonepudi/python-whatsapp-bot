from flask import Blueprint, request, jsonify
from app.services.whatsapp_service import send_bulk_initial_template
# Or use celery: from app.tasks.tasks import send_initial_message_to_users_async

bulk_send_bp = Blueprint("bulk_send", __name__)


@bulk_send_bp.route("/send-bulk", methods=["POST"])
def send_bulk_message():
    users = request.json.get("users", [])
    result = send_bulk_initial_template(users)  # For sync
    return jsonify({"status": "success", "result": result})
