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
        tools=[{"type": "file_search"}],  # "retrival" Before
        model="gpt-4o-mini",
    )


def check_if_thread_exists(wa_id):
    item = get_thread(wa_id)
    if not item or "thread_id" not in item:
        logging.warning("Thread record for %s is missing 'thread_id'", wa_id)
        return None
    return item["thread_id"]


def poll_until_complete(thread_id, run_id, timeout_secs=30, poll_interval=0.3):
    """
    Poll the run until it completes or fails.
    Returns (completed: bool, status: str, last_error: Any)
    """
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        if run.status == "completed":
            return True, run.status, None
        if run.status in ("failed", "cancelled", "expired"):
            return False, run.status, getattr(run, "last_error", None)
        time.sleep(poll_interval)

    # Timeout: fetch one more time so we can log the actual status/last_error
    run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
    return False, run.status, getattr(run, "last_error", None)


def run_assistant(thread_id, name, retries=3, delay=2, extra_instructions: str = ""):
    """
    Start a run on the given thread and return the NEWEST assistant reply.
    - Appends POLICY_INSTRUCTIONS and any extra_instructions you pass.
    - Logs run status/last_error on failure or timeout.
    """
    for attempt in range(retries):
        try:
            assistant = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)

            # Sanity check: make sure file_search is actually enabled on THIS assistant
            tool_types = [getattr(t, "type", None) for t in (assistant.tools or [])]
            if "file_search" not in tool_types:
                logging.error(
                    "[run_assistant] Assistant %s does not have file_search enabled. tools=%s",
                    assistant.id,
                    tool_types,
                )

            base = (
                f"You are talking to {name}, a job candidate. "
                "Be warm, professional, and helpful. Avoid repetition. "
                "Respond directly to the candidate's latest message."
            )
            instructions = base + "\n\n" + POLICY_INSTRUCTIONS
            if extra_instructions:
                instructions += "\n\n" + extra_instructions

            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=assistant.id,
                instructions=instructions,
            )

            completed, status, last_error = poll_until_complete(thread_id, run.id)
            if not completed:
                logging.error(
                    "[run_assistant] Run did not complete. status=%s last_error=%s",
                    status,
                    last_error,
                )
                return None

            # NEWEST → OLDEST; return the first assistant message
            msgs = client.beta.threads.messages.list(thread_id=thread_id, limit=50)
            for msg in msgs.data:
                if msg.role == "assistant":
                    parts = []
                    for part in msg.content:
                        text = getattr(part, "text", None)
                        if text and getattr(text, "value", None):
                            parts.append(text.value)
                    reply = "\n".join(parts).strip() if parts else ""
                    if reply:
                        return reply

            logging.error(
                "[run_assistant] No assistant message found after a completed run."
            )
            return None

        except openai.InternalServerError as e:
            logging.warning(
                "[run_assistant] Attempt %d/%d - OpenAI server error: %s",
                attempt + 1,
                retries,
                e,
            )
            time.sleep(delay)
        except Exception as e:
            logging.exception(
                "[run_assistant] Unhandled error on attempt %d: %r", attempt + 1, e
            )
            raise

    raise RuntimeError("Failed to retrieve assistant after retries")


POLICY_INSTRUCTIONS = """
You CAN accept and process resume uploads sent as WhatsApp documents.
If the user asks “Can I upload my resume here?”, answer YES and instruct them:
- Upload as PDF/DOCX (max ~100 MB).
If no file is provided yet, ask them to upload it.
Always give one clear next step; avoid generic 'How can I help?' replies.
"""


def wait_until_idle(thread_id: str, timeout: float = 12.0, poll: float = 0.3) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = client.beta.threads.runs.list(thread_id=thread_id, limit=1)
        busy = runs.data and runs.data[0].status in (
            "in_progress",
            "queued",
            "requires_action",
        )
        if not busy:
            return True
        time.sleep(poll)
    return False


def safe_add_message_to_thread(
    thread_id: str, content: str, wa_id: str, retries: int = 5, delay: float = 0.6
):
    tag = f"{wa_id}:{uuid.uuid4().hex[:8]}"
    for attempt in range(retries):
        wait_until_idle(thread_id, timeout=5)
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=f"{content}\n\n[MSG_TAG:{tag}]",
            metadata={"wa_id": wa_id, "msg_tag": tag},
        )
        # verify it's the newest user
        msgs = client.beta.threads.messages.list(thread_id=thread_id, limit=10)
        for m in msgs.data:  # newest → oldest
            if m.role == "user":
                meta = getattr(m, "metadata", None) or {}
                text = (
                    m.content
                    and getattr(m.content[0], "text", None)
                    and m.content[0].text.value
                ) or ""
                if meta.get("msg_tag") == tag or text.endswith(f"[MSG_TAG:{tag}]"):
                    return tag
                break
        logging.warning(
            "Top user turn not ours; retrying add (attempt %d)", attempt + 1
        )
        time.sleep(delay)
    raise RuntimeError("Could not verify user message was added to the thread")


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
            safe_add_message_to_thread(thread_id, user_message, wa_id)
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


def handle_candidate_reply(message, wa_id, name):
    if "update" in message.lower().strip():
        return "Sure! Please upload your updated resume and our team will review it shortly."
    return generate_response(
        f"Candidate has replied with '{message}'. Process the reply and respond accordingly.",
        wa_id,
        name,
    )


def generate_response(message_body, wa_id, name, extra_instructions: str = ""):
    thread_id = check_if_thread_exists(wa_id)
    if not thread_id:
        thread = client.beta.threads.create()
        thread_id = thread.id
        save_thread(wa_id, thread_id)

    # Persist and add to thread
    msg_id_user = str(uuid.uuid4())
    save_message(wa_id, msg_id_user, message_body, "user")

    tag = safe_add_message_to_thread(thread_id, message_body, wa_id)  # <-- NEW

    response = run_assistant(thread_id, name, extra_instructions=extra_instructions)
    if not response:
        response = "Sorry, I couldn't process that right now. Please try again shortly."

    # Save assistant reply
    msg_id_assistant = str(uuid.uuid4())
    save_message(wa_id, msg_id_assistant, response, "assistant")
    return response


def analyze_uploaded_document_with_gpt(
    wa_id: str, name: str, file_bytes: bytes, filename: str, content_type: str
) -> dict:
    try:
        file_obj = (filename, file_bytes, content_type)
        openai_file = client.files.create(file=file_obj, purpose="assistants")

        temp_thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=temp_thread.id,
            role="user",
            content=(
                "Please check the uploaded document and say if it's a valid resume. "
                'Respond ONLY as JSON: {"is_resume": true/false, "reason": "..."}'
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
                'Return strictly JSON with keys "is_resume" (boolean) and "reason" (string).'
            ),
            metadata={"kind": "resume_check"},
        )

        # poll
        import time, logging, json as _json

        for _ in range(40):
            s = client.beta.threads.runs.retrieve(
                thread_id=temp_thread.id, run_id=run.id
            )
            if s.status == "completed":
                break
            if s.status in ("failed", "cancelled", "expired"):
                logging.error("Resume check run failed: %s", s.last_error)
                return None
            time.sleep(0.25)

        msgs = client.beta.threads.messages.list(thread_id=temp_thread.id, limit=10)
        for m in msgs.data:
            if m.role == "assistant":
                raw = m.content[0].text.value
                try:
                    return _json.loads(raw)
                except _json.JSONDecodeError:
                    return None
        return None
    except Exception:
        import logging

        logging.exception("Error analyzing document with GPT:")
        return None
