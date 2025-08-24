# app/services/sqs.py
import json
import os
import uuid
import logging
import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Initialize SQS client (region from env, defaults to us-east-2)
sqs_client = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-2"))

# Target queue URL (set in .env or App Runner secrets)
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")


def push_message_to_sqs(message_dict: dict):
    """
    Push a single WhatsApp message event to SQS for processing.
    - If the queue is FIFO (URL ends with .fifo), we set:
        MessageGroupId = wa_id       -> guarantees per-user ordering
        MessageDeduplicationId = message_id (or uuid) -> idempotency window
    - For Standard queues, these fields are omitted.
    """
    if not SQS_QUEUE_URL:
        raise ValueError("SQS_QUEUE_URL environment variable not set")

    try:
        wa_id = message_dict.get("wa_id") or "unknown"
        dedup_id = message_dict.get("message_id") or f"{wa_id}-{uuid.uuid4().hex}"

        params = {
            "QueueUrl": SQS_QUEUE_URL,
            "MessageBody": json.dumps(message_dict),
        }

        # If using FIFO, add group & dedup to enforce per-user ordering + idempotency
        if SQS_QUEUE_URL.endswith(".fifo"):
            params["MessageGroupId"] = wa_id
            params["MessageDeduplicationId"] = dedup_id

        response = sqs_client.send_message(**params)

        logging.info("Message pushed to SQS: %s", response.get("MessageId"))
        logging.info("Pushed to SQS payload: %s", json.dumps(message_dict))
        return response

    except (BotoCoreError, ClientError) as e:
        logging.exception("Failed to push message to SQS")
        raise
