import logging
import os
import time
import json
import uuid
from dotenv import load_dotenv
import openai
from openai import OpenAI
from app.services.dynamodb import (
    get_thread,
    save_message,
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


def run_assistant(thread_id, name, retries=3, delay=2):
    """
    Start a run on the given thread (assumes the latest user message has already
    been added to the thread) and return the NEWEST assistant reply.

    Key differences vs before:
    - Do NOT reverse the messages list (the SDK already returns newest → oldest).
    - Return the first assistant message in that newest-first list.
    - Concatenate multiple text parts in the assistant message (if any).
    """
    for attempt in range(retries):
        try:
            # Fetch the assistant config (by id you already created)
            assistant = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)

            instructions = (
                f"You are talking to {name}, a job candidate. "
                "Be warm, professional, and helpful. Avoid repeating the same answers. "
                "Use previous thread context and respond directly to the candidate's latest message."
            )

            # Create a run on this thread
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=assistant.id,
                instructions=instructions,
            )

            # Wait for completion or failure
            if not poll_until_complete(thread_id, run.id):
                logging.warning(
                    "Assistant run did not complete for thread %s", thread_id
                )
                return None

            # Messages are returned NEWEST → OLDEST. Grab the first assistant message.
            msgs = client.beta.threads.messages.list(thread_id=thread_id, limit=50)
            for msg in msgs.data:  # newest first
                if msg.role == "assistant":
                    # Concatenate all text segments if present (attachments/tool outputs are ignored)
                    parts = []
                    for part in msg.content:
                        text = getattr(part, "text", None)
                        if text and getattr(text, "value", None):
                            parts.append(text.value)
                    reply = "\n".join(parts).strip() if parts else ""
                    if reply:
                        return reply

            logging.error("No assistant response found in thread: %s", thread_id)
            return None

        except openai.InternalServerError as e:
            logging.warning(
                "[run_assistant] Attempt %d/%d - OpenAI server error: %s",
                attempt + 1,
                retries,
                e,
            )
            time.sleep(delay)

        except Exception:
            logging.exception(
                "[run_assistant] Unhandled error on attempt %d", attempt + 1
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
    """
    Handles generating assistant response:
    1. Checks/creates thread
    2. Adds user message to thread and DB
    3. Triggers assistant run
    4. Stores and returns assistant response
    """
    thread_id = check_if_thread_exists(wa_id)
    if not thread_id:
        logging.info("Creating new thread for %s", wa_id)
        thread = client.beta.threads.create()
        thread_id = thread.id
        save_thread(wa_id, thread_id)
        time.sleep(0.3)

    # Save user message to DB
    msg_id_user = str(uuid.uuid4())
    save_message(wa_id, msg_id_user, message_body, "user")

    # Add user message to OpenAI thread
    try:
        safe_add_message_to_thread(thread_id, message_body)
    except Exception as e:
        logging.error("Failed to add message to thread: %s", e)
        return "Sorry, we couldn't process your message right now."

    # Run assistant and get response
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


def analyze_uploaded_document_with_gpt(
    wa_id: str, name: str, file_bytes: bytes, filename: str, content_type: str
) -> dict:
    try:
        file_obj = (filename, file_bytes, content_type)
        openai_file = client.files.create(file=file_obj, purpose="assistants")

        # Use a TEMP thread for analysis, not the chat thread
        temp_thread = client.beta.threads.create()

        client.beta.threads.messages.create(
            thread_id=temp_thread.id,
            role="user",
            content=(
                "Please check the uploaded document and tell me if it's a valid resume. "
                "Respond ONLY in JSON with keys is_resume (boolean) and reason (string)."
            ),
            attachments=[
                {"file_id": openai_file.id, "tools": [{"type": "file_search"}]}
            ],
            metadata={"kind": "resume_check"},
        )

        run = client.beta.threads.runs.create(
            thread_id=temp_thread.id,
            assistant_id=OPENAI_ASSISTANT_ID,
            instructions=(
                f"You are reviewing a document uploaded by {name}. "
                'Return strictly: {"is_resume": true/false, "reason": "..."}'
            ),
            metadata={"kind": "resume_check"},
        )

        # Poll
        for _ in range(40):
            status = client.beta.threads.runs.retrieve(
                thread_id=temp_thread.id, run_id=run.id
            )
            if status.status == "completed":
                break
            if status.status in ("failed", "cancelled", "expired"):
                logging.error("Document run failed: %s", status.last_error)
                return None
            time.sleep(0.25)

        # Read newest assistant message from temp thread only
        messages = client.beta.threads.messages.list(thread_id=temp_thread.id, limit=10)
        for msg in messages.data:
            if msg.role == "assistant":
                raw = msg.content[0].text.value
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    logging.warning("Non-JSON resume check response: %s", raw)
                    return None
        return None
    except Exception:
        logging.exception("Error analyzing document with GPT:")
        return None
