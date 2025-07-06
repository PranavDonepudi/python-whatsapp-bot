# run_worker.py
import boto3
import json
import logging
import os
import time
from botocore.exceptions import ClientError

from app.tasks.gpt_reply_worker import handle_gpt_reply

logging.basicConfig(level=logging.INFO)

# Configure SQS
sqs = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-2"))
QUEUE_URL = os.getenv("SQS_QUEUE_URL")


def poll_sqs():
    logging.info("[Worker] Starting polling loop...")

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=10,  # long polling to reduce cost
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

                    # Call your GPT logic here
                    handle_gpt_reply(body)

                    # Delete message after processing
                    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)
                    logging.info(f"[Worker] Deleted message from queue.")

                except Exception as e:
                    logging.exception(f"[Worker] Failed to process message: {e}")

        except ClientError as e:
            logging.error(f"[Worker] AWS ClientError: {e}")
            time.sleep(5)  # backoff before retry


if __name__ == "__main__":
    poll_sqs()
