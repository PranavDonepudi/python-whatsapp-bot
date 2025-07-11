import logging
import os
import time
import boto3
import uuid
from dotenv import load_dotenv
import openai
from openai import OpenAI
from app.services.dynamodb import (
    get_thread,
    save_message,
    get_recent_messages,
)
from app.services.dynamodb import save_thread

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
client = OpenAI(api_key=OPENAI_API_KEY)


def create_assistant():
    return client.beta.assistants.create(
        name="WhatsApp Recruitment Assistant",
        instructions=(
            "You are a professional assistant for TechnoGen. Help candidates understand job opportunities. "
            "Use professional, warm language. Keep responses concise (under 300 words / 500 tokens). "
        ),
        tools=[{"type": "retrieval"}],
        model="gpt-3.5-turbo-1106",
    )


def check_if_thread_exists(wa_id):
    item = get_thread(wa_id)
    if not item or "thread_id" not in item:
        logging.warning("Thread record for %s is missing 'thread_id'", wa_id)
        return None
    return item["thread_id"]


def poll_until_complete(thread_id, run_id, timeout_secs=10, poll_interval=0.3):
    for _ in range(int(timeout_secs / poll_interval)):
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        if run.status == "completed":
            return True
        elif run.status in ("failed", "cancelled", "expired"):
            logging.error(
                "Run failed for thread %s. Error: %s", thread_id, run.last_error
            )
            return False
        time.sleep(poll_interval)
    logging.error("Timeout: Run did not complete for thread: %s", thread_id)
    return False


def run_assistant(thread_id, name, message_body=None, retries=3, delay=2):
    for attempt in range(retries):
        try:
            assistant = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)

            if message_body:
                instructions = (
                    f"You are talking to {name}, a job candidate. "
                    f"Their message is: '{message_body}'. "
                    "Be warm and professional. Keep the response under 500 tokens."
                )
            else:
                instructions = (
                    f"You are talking to {name}, a job candidate. "
                    "Be warm and professional. Keep the response under 500 tokens."
                )

            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=assistant.id,
                instructions=instructions,
            )

            if not poll_until_complete(thread_id, run.id):
                raise RuntimeError(f"Run failed or timed out for thread {thread_id}")

            messages = client.beta.threads.messages.list(thread_id=thread_id, limit=5)
            for msg in reversed(messages.data):
                if msg.role == "assistant":
                    return msg.content[0].text.value

            raise ValueError("No assistant message found")

        except openai.InternalServerError as e:
            logging.warning(
                f"[run_assistant] Attempt {attempt + 1}/{retries} - OpenAI server error: {e}"
            )
            time.sleep(delay)

        except Exception as e:
            logging.exception(
                f"[run_assistant] Unhandled error on attempt {attempt + 1}"
            )
            raise

    raise RuntimeError("[run_assistant] Failed to retrieve assistant after retries")


def safe_add_message_to_thread(
    thread_id: str, content: str, retries: int = 5, delay: float = 1.0
):
    for attempt in range(retries):
        try:
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=content,
            )
            return
        except Exception as e:
            error_message = str(e)
            if "while a run" in error_message and attempt < retries - 1:
                logging.warning(
                    "Run active for thread %s. Retrying in %.1fs (attempt %d)...",
                    thread_id,
                    delay,
                    attempt + 1,
                )
                time.sleep(delay)
            else:
                logging.error("Failed to add message to thread: %s", error_message)
                raise


def is_active_run(thread_id):
    runs = client.beta.threads.runs.list(thread_id=thread_id)
    return any(
        run.status in ("in_progress", "queued", "requires_action") for run in runs.data
    )


def run_assistant_and_get_response(wa_id, name, user_message=None):
    thread_data = get_thread(wa_id)
    if not thread_data:
        logging.warning(f"No thread found for {wa_id}")
        return None

    thread_id = thread_data["thread_id"]
    logging.info(f"[run_assistant] Using thread: {thread_id} for {wa_id}")

    if user_message:
        try:
            logging.debug(f"Adding user message to thread: {user_message}")
            safe_add_message_to_thread(thread_id, user_message)
        except Exception as e:
            logging.error("Failed to add user message: %s", e)
            return None

    if is_active_run(thread_id):
        logging.warning("Run already active for thread %s, skipping.", thread_id)
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
        logging.info("Created run %s for thread %s", run.id, thread_id)

        if not poll_until_complete(thread_id, run.id):
            logging.warning("Run did not complete successfully.")
            return None

        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in reversed(messages.data):
            if msg.role == "assistant":
                response = msg.content[0].text.value
                logging.info(f"Assistant response: {response}")
                return response

        logging.error("No assistant response found in thread: %s", thread_id)
        return None

    except Exception as e:
        logging.exception("OpenAI assistant failed for thread %s: %s", thread_id, e)
        return None


def generate_response(message_body, wa_id, name):
    thread_id = check_if_thread_exists(wa_id)
    if thread_id:
        logging.info("Using existing thread for %s", wa_id)
    else:
        logging.info("Creating new thread for %s", wa_id)
        thread = client.beta.threads.create()
        thread_id = thread.id
        save_thread(wa_id, thread_id)

        # Let OpenAI hydrate the thread before writing to it
        time.sleep(0.3)

    # Collect recent context (reduce to last 2 messages for speed)
    context_messages = get_recent_messages(wa_id, limit=2)
    context_str = "\n".join(
        f"{m['message_type'].capitalize()}: {m['message_body'][:300]}"
        for m in reversed(context_messages)
        if m["message_body"]
    )

    prompt = (
        f"The candidate's name is {name}. This is a WhatsApp chat. "
        "Below are the last few messages exchanged. Use them for context. "
        "Do not include greetings, closing lines, or signatures.\n\n"
        f"{context_str}\n\n"
        f"Latest message: {message_body}"
    )

    # Save user message to DB
    msg_id_user = str(uuid.uuid4())
    save_message(wa_id, msg_id_user, message_body, "user")

    # Add prompt to OpenAI thread
    safe_add_message_to_thread(thread_id, prompt)

    # Run assistant and get response (with lower poll latency)
    response = run_assistant(thread_id, name)

    if not response:
        logging.warning(f"[generate_response] GPT gave no response for {wa_id}")
        return "Sorry, I couldn't process that right now. Please try again shortly."

    # Save assistant reply to DB
    msg_id_assistant = str(uuid.uuid4())
    save_message(wa_id, msg_id_assistant, response, "assistant")

    return response


def handle_candidate_reply(message, wa_id, name):
    if "update" in message.lower().strip():
        return "Sure! Please upload your updated resume and our team will review it shortly."
    return generate_response(
        f"Candidate has replied with '{message}'. Process the reply and respond accordingly.",
        wa_id,
        name,
    )
