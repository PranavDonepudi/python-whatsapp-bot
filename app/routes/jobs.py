# --- app/routes/jobs.py ---
from flask import Blueprint, request, jsonify
# from app.tasks.tasks import send_whatsapp_job_notification
# from app.utils.jobs import get_candidates_for_job

jobs_blueprint = Blueprint("jobs", __name__)


@jobs_blueprint.route("/send_whatsapp_bulk", methods=["POST"])
def send_bulk():
    job_id = request.json.get("job_id")
    candidates = get_candidates_for_job(job_id)

    for c in candidates:
        send_whatsapp_job_notification.delay(c["wa_id"], job_id)

    return jsonify({"status": "initiated", "total": len(candidates)})
