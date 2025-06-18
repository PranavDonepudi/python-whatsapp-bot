import logging
import os
import time

from app.services.dynamodb import (
    save_thread,
    get_thread,
    save_message,
    get_recent_messages,
)
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
client = OpenAI(api_key=OPENAI_API_KEY)

with open("data/JD.txt", "r", encoding="utf-8") as f:
    data = f.read()


def create_assistant():
    assistant = client.beta.assistants.create(
        name="WhatsApp Recruitment Assistant",
        instructions=(
            "You are a friendly and professional assistant for TechnoGen, an IT consulting company. Never repeat yourself or the instructions."
            "Your task is to explain job candidates about an available job position."
            "Never include internal URLs (e.g. S3 buckets), file paths, or database IDs in your answers. If asked about status, use human-friendly language only."
            "Also help them update their resumes and answer any questions they may have."
            "Use professional language and be warm in your responses. "
            "If a candidate responds with queries, answer based on typical recruitment scenarios. "
            "Here is the company job openings you should reference:" + data
        ),
        tools=[{"type": "retrieval"}],
        model="gpt-4-1106-preview",
    )
    return assistant


# Use context manager to ensure the shelf file is closed properly
def check_if_thread_exists(wa_id):
    item = get_thread(wa_id)
    return item["thread_id"] if item else None


def store_thread(wa_id, thread_id):
    save_thread(wa_id, thread_id)


def run_assistant(thread, name):
    # Retrieve the Assistant
    assistant = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)

    # Run the assistant
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id,
        instructions=f"You are talking to {name}, a job candidate. Be warm and professional. Keep the conversation focused on the job position or candidate's queries.",
    )

    # Wait for completion
    # https://platform.openai.com/docs/assistants/how-it-works/runs-and-run-steps#:~:text=under%20failed_at.-,Polling%20for%20updates,-In%20order%20to
    while run.status != "completed":
        # Be nice to the API
        time.sleep(0.5)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

    # Retrieve the Messages

    messages = client.beta.threads.messages.list(thread_id=thread.id)
    new_message = messages.data[0].content[0].text.value
    logging.info("Generated message: %s", new_message)
    return new_message


def handle_candidate_reply(message, wa_id, name):
    message_lower = message.lower().strip()

    if "update" in message_lower:
        response = "Sure! Please upload your updated resume and our team will review it shortly."
    else:
        response = generate_response(
            f"Candidate has replied with '{message}'. Process the reply and respond accordingly.",
            wa_id,
            name,
        )
    return response


def safe_add_message_to_thread(
    thread_id: str, content: str, retries: int = 5, delay: float = 1.0
):
    """
    Adds a message to the OpenAI thread, retrying if a run is still active.
    """
    for attempt in range(retries):
        try:
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=content,
            )
            return  # success
        except Exception as e:
            error_message = str(e)
            if "while a run" in error_message and attempt < retries - 1:
                logging.warning(
                    "Run active for thread %s. Retrying in %.1f seconds (attempt %d)...",
                    thread_id,
                    delay,
                    attempt + 1,
                )
                time.sleep(delay)
            else:
                logging.error(
                    "Failed to add message to thread after %d attempts: %s",
                    retries,
                    error_message,
                )
                raise


def is_active_run(thread_id):
    runs = client.beta.threads.runs.list(thread_id=thread_id)
    for run in runs.data:
        if run.status in ("in_progress", "queued", "requires_action"):
            return True
    return False


def run_assistant_and_get_response(wa_id, name, user_message=None):
    thread_data = get_thread(wa_id)
    if not thread_data:
        logging.warning(f"No thread found for {wa_id}")
        return None

    thread_id = thread_data["thread_id"]

    if user_message:
        try:
            safe_add_message_to_thread(thread_id, user_message)
        except Exception as e:
            logging.error("Failed to add user message to thread: %s", e)
            return None

    if is_active_run(thread_id):
        logging.info(f"Active run in progress for {thread_id}, skipping.")
        return None

    try:
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=OPENAI_ASSISTANT_ID,
            instructions=(
                f"You are talking to {name}, a job candidate. "
                "Be warm and professional. Keep the conversation focused."
            ),
        )

        for _ in range(20):  # ~20s timeout
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread_id, run_id=run.id
            ).status
            if run_status == "completed":
                break
            elif run_status in ("failed", "cancelled", "expired"):
                logging.warning("Run failed or cancelled for thread: %s", thread_id)
                return None
            time.sleep(1)

        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in reversed(messages.data):
            if msg.role == "assistant":
                return msg.content[0].text.value

    except Exception as e:
        logging.exception(f"Error running assistant for {wa_id}: {e}")
        return None


def generate_response(message_body, wa_id, name):
    # Get or create thread
    thread_id = check_if_thread_exists(wa_id)
    if not thread_id:
        logging.info("Creating new thread for %s with wa_id %s", name, wa_id)
        thread = client.beta.threads.create()
        thread_id = thread.id
        store_thread(wa_id, thread_id)
    else:
        logging.info("Retrieving existing thread for %s with wa_id %s", name, wa_id)
        thread = client.beta.threads.retrieve(thread_id)

    # Add current message to context
    context_messages = get_recent_messages(wa_id, limit=10)
    context_str = "\n".join(
        [
            f"{m['message_type'].capitalize()}: {m['message_body']}"
            for m in reversed(context_messages)
        ]
    )

    personalized_prompt = (
        f"The candidate's name is {name}. This is a WhatsApp chat. "
        "Below are the last few messages exchanged. Use them for context. "
        "Do not include greetings, closing lines, or signatures.\n\n"
        f"{context_str}\n\n"
        f"Latest message: {message_body}"
    )

    # Save user message
    save_message(wa_id, f"msg-{int(time.time())}", message_body, "user")

    # Add message to OpenAI thread
    safe_add_message_to_thread(thread_id, personalized_prompt)
    new_message = run_assistant(thread, name)

    # Save assistant response to message history
    save_message(wa_id, f"msg-{int(time.time()) + 1}", new_message, "assistant")

    return new_message
