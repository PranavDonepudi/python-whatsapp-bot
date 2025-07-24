# --- app/tasks/gpt_reply_worker.py ---
import logging
import uuid
import json
from celery_app import app
from app.services.dynamodb import get_thread, save_thread
from openai import OpenAI
from app.services.whatsapp_service import (
    send_message,
    get_text_message_input,
    process_text_for_whatsapp,
    download_whatsapp_media,
)
from app.tasks.background_tasks import (
    handle_document_upload_async,
)
from app.services.openai_service import (
    check_if_thread_exists,
    generate_response,
    analyze_uploaded_document_with_gpt,
)


client = OpenAI()


def handle_gpt_reply(payload):
    wa_id = payload["wa_id"]
    name = payload.get("name", "Candidate")
    media_id = payload.get("media_id")
    filename = payload.get("filename") or f"{wa_id}_{uuid.uuid4()}.pdf"
    message_type = payload.get("message_type", "text")
    message_body = payload.get("message_body", "")

    logging.info("[GPT Worker] Handling message from %s: %s", wa_id, message_body)

    try:
        # Step 1: Check or create thread
        thread_id = check_if_thread_exists(wa_id)
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_thread(wa_id, thread_id)

        # Step 2: Handle document uploads separately
        if message_type == "document":
            try:
                # Step 1: Download file from WhatsApp
                file_bytes, filename, content_type = download_whatsapp_media(
                    media_id, filename
                )

                # Step 2: Analyze with GPT Assistant
                result = analyze_uploaded_document_with_gpt(
                    wa_id=wa_id,
                    name=name,
                    file_bytes=file_bytes,
                    filename=filename,
                    content_type=content_type,
                )

                # Step 3: Check GPT result
                if not result:
                    send_message(
                        get_text_message_input(
                            wa_id,
                            "Sorry, we couldn't verify your document right now. Please try again.",
                        )
                    )
                    return

                # Step 4: If it's a resume, acknowledge and push to background
                if result.get("is_resume"):
                    send_message(
                        get_text_message_input(
                            wa_id, "Thanks! We've received your resume."
                        )
                    )
                    # Trigger background task to handle document upload
                    handle_document_upload_async.delay(
                        wa_id, media_id, filename, thread_id
                    )

                else:
                    # Step 5: Not a resume — notify user
                    send_message(
                        get_text_message_input(
                            wa_id,
                            f"Sorry, this doesn't appear to be a resume.\nReason: {result.get('reason', 'No reason provided.')}",
                        )
                    )

            except Exception as e:
                logging.exception("Error handling document upload")
                send_message(
                    get_text_message_input(
                        wa_id,
                        "Something went wrong while processing your document. Please try again.",
                    )
                )
            return
        # Skip unsupported or empty messages
        if not message_body or message_type not in ["text", "document"]:
            logging.warning(
                "[GPT Worker] Skipping unsupported or empty message from %s", wa_id
            )
            return
        # Step 3: Generate GPT response using context
        try:
            reply = generate_response(message_body, wa_id, name)
        except Exception as gpt_error:
            logging.exception("[GPT Worker] GPT failed for %s: %s", wa_id, gpt_error)
            fallback = "Sorry, we're facing a temporary issue. Please try again in a few minutes."
            send_message(get_text_message_input(wa_id, fallback))
            return

        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )
            logging.info("[GPT Worker] Replied to %s", wa_id)
        else:
            logging.warning(f"[GPT Worker] No assistant reply for {wa_id}")

    except Exception as e:
        logging.exception(f"[GPT Worker] Failed to process message for {wa_id}: {e}")
