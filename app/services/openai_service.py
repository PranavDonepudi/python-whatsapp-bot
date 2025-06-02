from openai import OpenAI
import shelve
from dotenv import load_dotenv
import os
import time
import logging

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
client = OpenAI(api_key=OPENAI_API_KEY)


def create_assistant():
    """
    You currently cannot set the temperature for Assistant via the API.
    """
    assistant = client.beta.assistants.create(
        name="WhatsApp Recruitment Assistant",
        instructions="You are a friendly and professional assistant for TechnoGen, an IT consulting company. "
        "Your task is to inform job candidates when they are selected for roles, provide helpful follow-up, "
        "and guide them on next steps (e.g., submitting updated resumes, scheduling interviews, etc). "
        "If a candidate responds with queries, answer based on typical recruitment scenarios. "
        "If unsure, suggest they reach out to the TechnoGen team.",
        tools=[{"type": "retrieval"}],
        model="gpt-4-1106-preview",
    )
    return assistant


# Use context manager to ensure the shelf file is closed properly
def check_if_thread_exists(wa_id):
    with shelve.open("threads_db") as threads_shelf:
        return threads_shelf.get(wa_id, None)


def store_thread(wa_id, thread_id):
    with shelve.open("threads_db", writeback=True) as threads_shelf:
        threads_shelf[wa_id] = thread_id


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
    elif "yes" in message_lower:
        # You can modify this prompt if needed
        response = generate_response(
            "The candidate agreed to proceed. What should we say next?", wa_id, name
        )
    elif "no" in message_lower:
        response = "No problem. Let us know if you would like to be considered for future opportunities!"
    else:
        response = generate_response(message, wa_id, name)

    return response


def generate_response(message_body, wa_id, name):
    # Check if there is already a thread_id for the wa_id
    thread_id = check_if_thread_exists(wa_id)

    # If a thread doesn't exist, create one and store it
    if thread_id is None:
        logging.info("Creating new thread for %s with wa_id %s", name, wa_id)
        thread = client.beta.threads.create()
        store_thread(wa_id, thread.id)
        thread_id = thread.id

    # Otherwise, retrieve the existing thread
    else:
        logging.info("Retrieving existing thread for %s with wa_id %s", name, wa_id)
        thread = client.beta.threads.retrieve(thread_id)

    # Add message to thread
    personalized_prompt = (
        f"The candidate's name is {name}. Respond to the following message as TechnoGen's recruitment bot. "
        "Do not include a signature, closing line, or mention 'Regards' or 'Your Name'. "
        "Just answer naturally and keep it short unless detailed information is needed. "
        f"Here is the message: {message_body} "
    )
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=personalized_prompt,
    )

    # Run the assistant and get the new message
    new_message = run_assistant(thread, name)

    return new_message
