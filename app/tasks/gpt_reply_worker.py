# --- app/tasks/gpt_reply_worker.py ---
import logging
import uuid
import re
from openai import OpenAI

from app.services.dynamodb import save_thread
from app.services.whatsapp_service import (
    send_message,
    get_text_message_input,
    process_text_for_whatsapp,
    download_whatsapp_media,
    save_file_to_s3,  # <-- use your existing S3 helper
)
from app.services.openai_service import (
    check_if_thread_exists,
    generate_response,
    analyze_uploaded_document_with_gpt,
)

client = OpenAI()

# simple detector so we always say "yes" when users ask about uploads
_UPLOAD_Q = re.compile(r"\b(upload|attach|send)\b.*\b(resume|cv|document|file)\b", re.I)


def handle_gpt_reply(payload):
    wa_id = payload["wa_id"]
    name = payload.get("name", "Candidate")
    media_id = payload.get("media_id")
    filename = payload.get("filename") or f"{wa_id}_{uuid.uuid4()}.pdf"
    message_type = payload.get("message_type", "text")
    message_body = (payload.get("message_body") or "").strip()

    logging.info(
        "[GPT Worker] Handling message from %s (type=%s): %s",
        wa_id,
        message_type,
        message_body[:200],
    )

    try:
        # Ensure an OpenAI thread exists for this user
        thread_id = check_if_thread_exists(wa_id)
        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            save_thread(wa_id, thread_id)
            logging.info("[GPT Worker] Created new thread %s for %s", thread_id, wa_id)

        # ========== DOCUMENT FLOW (synchronous upload) ==========
        if message_type == "document":
            try:
                # 1) Download media from WhatsApp
                file_bytes, effective_filename, content_type = download_whatsapp_media(
                    media_id, filename
                )
                filename = effective_filename or filename

                # 2) Analyze in a TEMP thread (keeps JSON out of chat thread)
                result = analyze_uploaded_document_with_gpt(
                    wa_id=wa_id,
                    name=name,
                    file_bytes=file_bytes,
                    filename=filename,
                    content_type=content_type,
                )

                if not result:
                    send_message(
                        get_text_message_input(
                            wa_id,
                            "Sorry, we couldn't verify your document right now. Please try again.",
                        )
                    )
                    return

                if result.get("is_resume"):
                    # 3) Upload to S3 synchronously
                    try:
                        s3_url = save_file_to_s3(file_bytes, filename, content_type)
                        logging.info("[GPT Worker] Uploaded resume to S3: %s", s3_url)
                    except Exception:
                        logging.exception(
                            "[GPT Worker] Synchronous S3 upload failed for %s", wa_id
                        )
                        send_message(
                            get_text_message_input(
                                wa_id,
                                "We couldn't process your document right now. Please try again.",
                            )
                        )
                        return

                    # 4) Acknowledge only after successful upload
                    send_message(
                        get_text_message_input(
                            wa_id, "Thanks! We've received your resume."
                        )
                    )
                else:
                    reason = result.get("reason", "No reason provided.")
                    send_message(
                        get_text_message_input(
                            wa_id,
                            f"Sorry, this doesn't appear to be a resume.\nReason: {reason}",
                        )
                    )

            except Exception:
                logging.exception("[GPT Worker] Error handling document for %s", wa_id)
                send_message(
                    get_text_message_input(
                        wa_id,
                        "Something went wrong while processing your document. Please try again.",
                    )
                )
            return  # end document branch

        # ========== TEXT FLOW ==========
        if message_type != "text" or not message_body:
            logging.warning(
                "[GPT Worker] Skipping unsupported or empty message from %s", wa_id
            )
            return

        # Friendly fast-path: if user asks about uploading, always say YES
        if _UPLOAD_Q.search(message_body):
            send_message(
                get_text_message_input(
                    wa_id,
                    "Yes — you can upload your resume here as a document (PDF/DOC/DOCX). "
                    "Once I receive it, I’ll analyze it and help you update or tailor it.",
                )
            )
            return

        # Normal assistant reply (context-aware)
        try:
            reply = generate_response(message_body, wa_id, name)
        except Exception as gpt_error:
            logging.exception("[GPT Worker] GPT failed for %s: %s", wa_id, gpt_error)
            send_message(
                get_text_message_input(
                    wa_id,
                    "Sorry, we're facing a temporary issue. Please try again in a few minutes.",
                )
            )
            return

        if reply:
            send_message(
                get_text_message_input(wa_id, process_text_for_whatsapp(reply))
            )
            logging.info("[GPT Worker] Replied to %s", wa_id)
        else:
            logging.warning("[GPT Worker] No assistant reply for %s", wa_id)

    except Exception as e:
        logging.exception("[GPT Worker] Failed to process message for %s: %s", wa_id, e)
