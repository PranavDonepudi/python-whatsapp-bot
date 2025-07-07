import json
import boto3
import os
import logging
from botocore.exceptions import BotoCoreError, ClientError

# Initialize SQS client
sqs_client = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-2"))

# Your target queue URL (make sure it's set in your .env or App Runner secrets)
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL")


def push_message_to_sqs(message_dict):
    """
    Push a single WhatsApp message event to SQS for processing.
    This is meant to be called from the webhook handler.
    """
    if not SQS_QUEUE_URL:
        raise ValueError("SQS_QUEUE_URL environment variable not set")

    try:
        response = sqs_client.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps(message_dict),
        )
        logging.info(f"Message pushed to SQS: {response.get('MessageId')}")
    except (BotoCoreError, ClientError) as e:
        logging.exception("Failed to push message to SQS")
        raise
