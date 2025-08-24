# smoke_openai.py
import os, time
from openai import OpenAI
from dotenv import load_dotenv

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_ASSISTANT_ID = os.environ["OPENAI_ASSISTANT_ID"]

client = OpenAI(api_key=OPENAI_API_KEY)

# Verify the assistant is real and runnable
a = client.beta.assistants.retrieve(OPENAI_ASSISTANT_ID)
print(
    "Assistant OK:",
    a.id,
    "model=",
    a.model,
    "tools=",
    [t.type for t in (a.tools or [])],
)

# Create a scratch thread and run
th = client.beta.threads.create()
client.beta.threads.messages.create(thread_id=th.id, role="user", content="ping")

run = client.beta.threads.runs.create(
    thread_id=th.id, assistant_id=a.id, instructions="Reply with 'pong' only."
)

# Poll
for _ in range(40):
    r = client.beta.threads.runs.retrieve(thread_id=th.id, run_id=run.id)
    if r.status == "completed":
        break
    if r.status in ("failed", "cancelled", "expired"):
        print("Run failed:", r.last_error)
        raise SystemExit(1)
    time.sleep(0.25)

msgs = client.beta.threads.messages.list(thread_id=th.id, limit=5)
print("LATEST:", msgs.data[0].role, msgs.data[0].content[0].text.value.strip())
