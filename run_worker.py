# run_worker.py

import boto3
import json
import logging
import os
import threading
import time
from flask import Flask
from botocore.exceptions import ClientError

from app.tasks.gpt_reply_worker import handle_gpt_reply

logging.basicConfig(level=logging.INFO)

# Configure SQS
sqs = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-2"))
QUEUE_URL = os.getenv("SQS_QUEUE_URL")

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health_check():
    return {"status": "ok"}, 200


def poll_sqs():
    logging.info("[Worker] Starting polling loop...")

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=10,  # long polling
                VisibilityTimeout=60,
            )

            messages = response.get("Messages", [])
            if not messages:
                continue

            for msg in messages:
                receipt_handle = msg["ReceiptHandle"]
                try:
                    body = json.loads(msg["Body"])
                    logging.info(f"[Worker] Received message: {body}")
                    handle_gpt_reply(body)
                    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)
                    logging.info("[Worker] Deleted message from queue.")
                except Exception as e:
                    logging.exception(f"[Worker] Failed to process message: {e}")

        except ClientError as e:
            logging.error(f"[Worker] AWS ClientError: {e}")
            time.sleep(5)


if __name__ == "__main__":
    logging.info("[Worker] Bootstrapping...")

    # Start SQS polling AFTER env vars are guaranteed to be available
    threading.Thread(target=poll_sqs, daemon=True).start()

    # Start Flask app (needed for App Runner health check)
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
