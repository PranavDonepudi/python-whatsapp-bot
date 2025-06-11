import boto3
import os
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key
import pprint
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


def get_thread(wa_id, thread_id):
    table = dynamodb.Table(get_threads_table())
    response = table.get_item(Key={"wa_id": wa_id, "thread_id": thread_id})
    return response.get("Item")


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


def get_recent_messages(wa_id, limit=10):
    table = dynamodb.Table(get_messages_table())
    response = table.query(
        KeyConditionExpression=Key("wa_id").eq(wa_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return response.get("Items", [])
