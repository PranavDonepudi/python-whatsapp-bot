# --- app/tasks/gpt_reply_worker.py ---
import json
import time
import logging
import os

from openai import OpenAI
from app.services.whatsapp_service import (
    send_message,
    get_text_message_input,
    process_text_for_whatsapp,
)

from app.services.openai_service import (
    check_if_thread_exists,
    store_thread,
    safe_add_message_to_thread,
    is_active_run,
)

client = OpenAI()


def handle_gpt_reply(payload):
    wa_id = payload["wa_id"]
    name = payload.get("name", "Candidate")
    message_body = payload.get("message", "")

    logging.info(f"[GPT Worker] Handling message from {wa_id}: {message_body}")

    try:
        # Step 1: Check or create thread
        thread_id = check_if_thread_exists(wa_id)
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            store_thread(wa_id, thread_id)

        # Step 2: Add user message to thread
        safe_add_message_to_thread(thread_id, message_body)

        # Step 3: Skip if assistant is already processing
        if is_active_run(thread_id):
            logging.warning(f"[GPT Worker] Active run in progress for {wa_id}")
            return

        # Step 4: Run assistant
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=os.getenv("OPENAI_ASSISTANT_ID"),
            instructions=f"You are talking to {name}, a job candidate. Be warm and professional.",
        )

        # Step 5: Poll for completion
        for _ in range(20):
            status = client.beta.threads.runs.retrieve(
                thread_id=thread_id, run_id=run.id
            ).status
            if status == "completed":
                break
            elif status in ("failed", "cancelled", "expired"):
                logging.error(f"[GPT Worker] Assistant run failed for {wa_id}")
                return
            time.sleep(1)

        # Step 6: Get assistant reply
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in reversed(messages.data):
            if msg.role == "assistant":
                reply_text = msg.content[0].text.value
                reply = process_text_for_whatsapp(reply_text)
                send_message(get_text_message_input(wa_id, reply))
                logging.info(f"[GPT Worker] Replied to {wa_id}")
                return

        logging.warning(f"[GPT Worker] No assistant message found for {wa_id}")

    except Exception as e:
        logging.exception(f"[GPT Worker] Failed to process message for {wa_id}: {e}")
