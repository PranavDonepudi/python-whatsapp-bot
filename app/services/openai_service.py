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
            "Here is the company data you should reference:" + data
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
        instructions=f"You are talking to {name}, a job candidate. Be warm and professional.",
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
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=personalized_prompt,
    )

    new_message = run_assistant(thread, name)

    # Save assistant response to message history
    save_message(wa_id, f"msg-{int(time.time()) + 1}", new_message, "assistant")

    return new_message
