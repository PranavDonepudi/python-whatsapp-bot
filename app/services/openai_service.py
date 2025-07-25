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
    Starts a new run using the thread_id. Assumes latest user message is already added to the thread.
    Returns the latest assistant response or fallback if none found.
    """
    for attempt in range(retries):
        try:
            assistant = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)

            instructions = (
                f"You are talking to {name}, a job candidate. "
                "Be warm, professional, and helpful. Avoid repeating the same answers. "
                "Do not include unnecessary closings like 'feel free to reach out again' unless the conversation is ending. "
                "Use previous thread context and respond directly to the candidate's latest message."
            )

            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=assistant.id,
                instructions=instructions,
            )

            if not poll_until_complete(thread_id, run.id):
                logging.warning(
                    "Assistant run did not complete for thread %s", thread_id
                )
                return None

            messages = client.beta.threads.messages.list(thread_id=thread_id, limit=5)
            for msg in reversed(messages.data):
                if msg.role == "assistant":
                    return msg.content[0].text.value

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
    """
    Uploads a document to OpenAI, asks assistant to validate if it's a resume,
    and returns a JSON object with 'is_resume' and 'reason'.
    """

    try:
        # 1. Upload file to OpenAI
        file_obj = (filename, file_bytes, content_type)
        openai_file = client.files.create(file=file_obj, purpose="assistants")

        # 2. Check or create thread
        thread_data = get_thread(wa_id)
        if not thread_data:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_thread(wa_id, thread_id)
        else:
            thread_id = thread_data["thread_id"]

        # 3. Add file to thread with prompt
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content="Please check the uploaded document and tell me if it's a valid resume. Respond in JSON.",
            attachments=[
                {"file_id": openai_file.id, "tools": [{"type": "file_search"}]}
            ],
        )

        # 4. Run assistant with strict JSON instruction
        instructions = (
            f"You are reviewing a document uploaded by {name}, a job candidate. "
            "Analyze the file and respond ONLY in this JSON format:\n\n"
            '{\n  "is_resume": true/false,\n  "reason": "..."\n}\n\n'
            "Consider it a resume only if it contains sections like 'Experience', 'Education', or 'Skills'."
        )

        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=OPENAI_ASSISTANT_ID,
            instructions=instructions,
        )

        # 5. Poll until complete
        for _ in range(30):  # up to ~9 seconds
            run_status = client.beta.threads.runs.retrieve(
                thread_id=thread_id, run_id=run.id
            )
            if run_status.status == "completed":
                break
            elif run_status.status in ("failed", "cancelled", "expired"):
                logging.error("Document run failed: %s", run_status.last_error)
                return None
            time.sleep(0.3)

        # 6. Get assistant response
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        for msg in reversed(messages.data):
            if msg.role == "assistant":
                raw_response = msg.content[0].text.value
                try:
                    result = json.loads(raw_response)
                    logging.info("Parsed GPT JSON: %s", result)
                    return result
                except json.JSONDecodeError:
                    logging.warning("GPT did not return valid JSON: %s", raw_response)
                    return None

        logging.warning("No assistant response found.")
        return None

    except Exception as e:
        logging.exception("Error analyzing document with GPT:")
        return None
