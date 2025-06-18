# --- app/utils/validators.py ---
def is_valid_whatsapp_message(body: dict) -> bool:
    try:
        value = body["entry"][0]["changes"][0]["value"]
        return bool(value.get("messages"))
    except (IndexError, KeyError):
        return False
