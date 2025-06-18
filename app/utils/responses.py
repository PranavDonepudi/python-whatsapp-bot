# --- app/utils/responses.py ---
from flask import jsonify


def respond_ok():
    return jsonify({"status": "ok"}), 200


def respond_error(message: str, code: int = 400):
    return jsonify({"status": "error", "message": message}), code
