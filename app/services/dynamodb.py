import boto3
import os
import logging
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key
from dotenv import load_dotenv

load_dotenv()
# Proper env fallbacks
THREADS_TABLE = os.getenv("THREADS_TABLE")
if not THREADS_TABLE or THREADS_TABLE == "THREADS_TABLE":
    THREADS_TABLE = "WhatsAppThreads"

MESSAGES_TABLE = os.getenv("MESSAGES_TABLE")
if not MESSAGES_TABLE or MESSAGES_TABLE == "MESSAGES_TABLE":
    MESSAGES_TABLE = "WhatsAppMessages"

# DynamoDB connection
dynamodb = boto3.resource(
    "dynamodb",
    region_name=os.getenv("AWS_REGION", "us-east-2"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)


def get_threads_table():
    return os.getenv("THREADS_TABLE", "WhatsAppThreads")


def get_messages_table():
    return os.getenv("MESSAGES_TABLE", "WhatsAppMessages")


def save_thread(wa_id, thread_id):
    table = dynamodb.Table(get_threads_table())
    table.put_item(
        Item={
            "wa_id": wa_id,
            "thread_id": thread_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def get_thread(wa_id):
    """
    Get the most recent thread (if any) for a given wa_id.
    Assumes wa_id is the partition key and thread_id is the sort key.
    """
    table = dynamodb.Table(get_threads_table())
    response = table.query(
        KeyConditionExpression=Key("wa_id").eq(wa_id), ScanIndexForward=False, Limit=1
    )
    items = response.get("Items", [])
    return items[0] if items else None


def save_message(wa_id, message_id, body, msg_type):
    table = dynamodb.Table(get_messages_table())
    table.put_item(
        Item={
            "wa_id": wa_id,
            "message_id": message_id,
            "message_body": body,
            "message_type": msg_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def is_duplicate_message(message_id):
    table = dynamodb.Table(
        "ProcessedMessages"
    )  # create this table with message_id as PK
    try:
        response = table.get_item(Key={"message_id": message_id})
        return "Item" in response
    except Exception as e:
        logging.error("Failed to check duplicate: %s", e)
        return False


def mark_message_as_processed(message_id):
    table = dynamodb.Table("ProcessedMessages")
    try:
        table.put_item(
            Item={
                "message_id": message_id,
                "processed_at": datetime.utcnow().isoformat(),
            }
        )
    except Exception as e:
        logging.error("Failed to mark message as processed: %s", e)


def get_recent_messages(wa_id, limit=10):
    table = dynamodb.Table(get_messages_table())
    response = table.query(
        KeyConditionExpression=Key("wa_id").eq(wa_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return response.get("Items", [])
