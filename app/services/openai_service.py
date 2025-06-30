import logging
import os
import time
import uuid
from dotenv import load_dotenv
from openai import OpenAI
from app.services.dynamodb import (
    save_thread,
    get_thread,
    save_message,
    get_recent_messages,
)

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
client = OpenAI(api_key=OPENAI_API_KEY)

# Load job description once
with open("data/JD.txt", "r", encoding="utf-8") as f:
    job_description_data = f.read()


# ========== Assistant Setup ==========
def create_assistant():
    return client.beta.assistants.create(
        name="WhatsApp Recruitment Assistant",
        instructions=(
            "You are a friendly and professional assistant for TechnoGen, an IT consulting company. "
            "Use professional and warm language. Help candidates understand job opportunities and answer questions. "
            "Reference the job listings below:\n" + job_description_data
        ),
        tools=[{"type": "retrieval"}],
        model="gpt-4-1106-preview",
    )


# ========== Helper Utilities ==========


def poll_until_complete(thread_id, run_id, timeout_secs=20):
    for _ in range(timeout_secs):
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        logging.debug(f"[poll] Run status: {run.status}")
        if run.status == "completed":
            return True
        elif run.status in ("failed", "cancelled", "expired"):
            logging.error(
                "Run failed for thread %s. Error: %s", thread_id, run.last_error
            )
            return False
        time.sleep(1)
    logging.error("Timeout: Run did not complete for thread: %s", thread_id)
    return False


def safe_add_message_to_thread(thread_id, content, retries=5, delay=1.0):
    for attempt in range(retries):
        try:
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=content,
            )
            return
        except Exception as e:
            if "while a run" in str(e) and attempt < retries - 1:
                logging.warning(
                    "Run active for thread %s. Retrying (attempt %d)...",
                    thread_id,
                    attempt + 1,
                )
                time.sleep(delay)
            else:
                logging.error("Failed to add message to thread %s: %s", thread_id, e)
                raise


def is_active_run(thread_id):
    runs = client.beta.threads.runs.list(thread_id=thread_id)
    return any(
        run.status in ("in_progress", "queued", "requires_action") for run in runs.data
    )


# ========== Main Assistant Logic ==========


def get_or_create_thread(wa_id):
    item = get_thread(wa_id)
    if item:
        return item["thread_id"]
    new_thread = client.beta.threads.create()
    save_thread(wa_id, new_thread.id)
    return new_thread.id


def run_assistant_with_context(thread_id, assistant_id, name):
    run = client.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=assistant_id,
        instructions=f"You are chatting with {name}. Respond professionally and clearly.",
    )
    logging.info("Created run %s for thread %s", run.id, thread_id)

    if not poll_until_complete(thread_id, run.id):
        logging.warning("Run did not complete successfully.")
        return None

    messages = client.beta.threads.messages.list(thread_id=thread_id)
    for msg in reversed(messages.data):
        if msg.role == "assistant":
            return msg.content[0].text.value
    logging.error("No assistant response found for thread %s", thread_id)
    return None


def generate_response(message_body, wa_id, name):
    thread_id = get_or_create_thread(wa_id)
    logging.info("Using thread %s for %s", thread_id, wa_id)

    # Collect and trim context
    context_messages = get_recent_messages(wa_id, limit=4)
    context_str = "\n".join(
        f"{m['message_type'].capitalize()}: {m['message_body'][:300]}"  # trim long messages
        for m in reversed(context_messages)
    )

    prompt = (
        f"The candidate's name is {name}. This is a WhatsApp conversation. "
        f"Use the following chat history as context.\n\n{context_str}\n\nLatest message: {message_body}"
    )

    # Save and send message
    save_message(wa_id, str(uuid.uuid4()), message_body, "user")
    safe_add_message_to_thread(thread_id, prompt)
    response = run_assistant_with_context(thread_id, OPENAI_ASSISTANT_ID, name)

    if response:
        save_message(wa_id, str(uuid.uuid4()), response, "assistant")
    return response


# ========== Entry Point ==========


def handle_candidate_reply(message, wa_id, name):
    if "update" in message.lower().strip():
        return "Sure! Please upload your updated resume and our team will review it shortly."
    return generate_response(f"Candidate has replied with: '{message}'", wa_id, name)
