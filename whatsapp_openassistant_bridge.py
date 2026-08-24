#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import secrets
import signal
import tempfile
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv, set_key
from flask import Flask, jsonify, request
from neonize.client import ClientFactory, NewClient
from neonize.events import ConnectedEv, MessageEv
from neonize.utils.jid import Jid2String, build_jid

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
load_dotenv(ENV_FILE, override=False)


def only_digits(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_attr(obj: Any, *names: str) -> Any:
    if obj is None:
        return None
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return None


def prompt_value(label: str, default: str = "", required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix}: ").strip() or default
        if value or not required:
            return value
        print("This value is required.")


def save_env(values: dict[str, str]) -> None:
    if not ENV_FILE.exists():
        ENV_FILE.touch(mode=0o600)
    for key, value in values.items():
        set_key(str(ENV_FILE), key, value, quote_mode="never")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def run_setup() -> None:
    print("\n=== WhatsApp <-> Open Assistant initial setup ===\n")
    allowed = only_digits(prompt_value(
        "Allowed WhatsApp number without +",
        only_digits(os.getenv("ALLOWED_WHATSAPP_NUMBER", "")),
        required=True,
    ))
    if len(allowed) < 8:
        raise SystemExit("The phone number is too short.")

    oa_url = prompt_value(
        "Open Assistant chat URL",
        os.getenv("OPENASSISTANT_URL", "http://127.0.0.1:8080/api/chat/stream"),
        required=True,
    )
    upload_url = prompt_value(
        "Open Assistant upload URL",
        os.getenv("OPENASSISTANT_UPLOAD_URL", "http://127.0.0.1:8080/api/upload"),
        required=True,
    )
    oa_token = prompt_value("Open Assistant bearer token (Enter = none)", os.getenv("OPENASSISTANT_TOKEN", ""))
    channel = prompt_value("Open Assistant Channel", os.getenv("OPENASSISTANT_CHANNEL", "whatsapp"), required=True)
    bridge_token = os.getenv("BRIDGE_TOKEN", "").strip() or secrets.token_hex(32)

    save_env({
        "ALLOWED_WHATSAPP_NUMBER": allowed,
        "OPENASSISTANT_URL": oa_url,
        "OPENASSISTANT_UPLOAD_URL": upload_url,
        "OPENASSISTANT_TOKEN": oa_token,
        "OPENASSISTANT_CHANNEL": channel,
        "OPENASSISTANT_IMAGE_CHANNEL": os.getenv(
            "OPENASSISTANT_IMAGE_CHANNEL",
            "webui",
        ),
        "BRIDGE_TOKEN": bridge_token,
        "WHATSAPP_DB": os.getenv("WHATSAPP_DB", "./data/whatsapp.db"),
        "CONVERSATION_STATE_FILE": os.getenv("CONVERSATION_STATE_FILE", "./data/conversation_state.json"),
        "TEMP_IMAGE_DIR": os.getenv("TEMP_IMAGE_DIR", "./data/tmp"),
        "HOST": os.getenv("HOST", "127.0.0.1"),
        "PORT": os.getenv("PORT", "8081"),
        "REQUEST_TIMEOUT": os.getenv("REQUEST_TIMEOUT", "180"),
        "MAX_IMAGE_MB": os.getenv("MAX_IMAGE_MB", "20"),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
        "IGNORE_GROUPS": os.getenv("IGNORE_GROUPS", "true"),
        "ALLOW_SEND_TO_ANY": os.getenv("ALLOW_SEND_TO_ANY", "false"),
        "CLEAR_COMMAND": os.getenv("CLEAR_COMMAND", "/clear"),
        "DEFAULT_IMAGE_PROMPT": os.getenv("DEFAULT_IMAGE_PROMPT", "What can be seen in this image?"),
    })
    print(f"\n✓ Configuration saved: {ENV_FILE}")
    print("✓ Images are only stored temporarily and deleted after processing.\n")


parser = argparse.ArgumentParser()
parser.add_argument("--init", action="store_true")
args = parser.parse_args()
if args.init:
    run_setup()
    raise SystemExit(0)

if not only_digits(os.getenv("ALLOWED_WHATSAPP_NUMBER", "")):
    if os.isatty(0):
        run_setup()
        load_dotenv(ENV_FILE, override=True)
    else:
        raise SystemExit("ALLOWED_WHATSAPP_NUMBER is missing. Run once with --init.")

ALLOWED_NUMBER = only_digits(os.getenv("ALLOWED_WHATSAPP_NUMBER", ""))
OA_URL = os.getenv("OPENASSISTANT_URL", "http://127.0.0.1:8080/api/chat/stream").strip()
OA_UPLOAD_URL = os.getenv("OPENASSISTANT_UPLOAD_URL", "http://127.0.0.1:8080/api/upload").strip()
OA_TOKEN = os.getenv("OPENASSISTANT_TOKEN", "").strip()
OA_CHANNEL = os.getenv("OPENASSISTANT_CHANNEL", "whatsapp").strip()
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()
CLEAR_COMMAND = os.getenv("CLEAR_COMMAND", "/clear").strip().lower()
DEFAULT_IMAGE_PROMPT = os.getenv("DEFAULT_IMAGE_PROMPT", "What can be seen in this image?").strip()
HOST = os.getenv("HOST", "127.0.0.1").strip()
PORT = int(os.getenv("PORT", "8081"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "180"))
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "20"))
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
IGNORE_GROUPS = env_bool("IGNORE_GROUPS", True)
ALLOW_SEND_TO_ANY = env_bool("ALLOW_SEND_TO_ANY", False)


def resolve_path(value: str) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    p = p.resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

DB_PATH = resolve_path(os.getenv("WHATSAPP_DB", "./data/whatsapp.db"))
CONVERSATION_STATE_FILE = resolve_path(os.getenv("CONVERSATION_STATE_FILE", "./data/conversation_state.json"))
TEMP_IMAGE_DIR = resolve_path(os.getenv("TEMP_IMAGE_DIR", "./data/tmp/x")).parent
TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(name)s %(levelname)s] - %(message)s")
log = logging.getLogger("wa-openassistant")

conversation_lock = threading.Lock()
conversation_id = ""
conversation_day = ""


def today_string() -> str:
    return date.today().isoformat()


def save_conversation_state() -> None:
    payload = {"conversation_id": conversation_id, "day": conversation_day}
    tmp = CONVERSATION_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONVERSATION_STATE_FILE)
    try:
        CONVERSATION_STATE_FILE.chmod(0o600)
    except OSError:
        pass


def create_new_conversation(reason: str) -> str:
    global conversation_id, conversation_day
    with conversation_lock:
        conversation_id = str(uuid.uuid4())
        conversation_day = today_string()
        save_conversation_state()
        log.info("New conversation ID (%s): %s", reason, conversation_id)
        return conversation_id


def load_conversation_state() -> None:
    global conversation_id, conversation_day
    try:
        if CONVERSATION_STATE_FILE.exists():
            data = json.loads(CONVERSATION_STATE_FILE.read_text(encoding="utf-8"))
            if data.get("conversation_id") and data.get("day") == today_string():
                conversation_id = str(data["conversation_id"])
                conversation_day = str(data["day"])
                return
    except Exception:
        log.exception("Could not read the conversation state.")
    create_new_conversation("startup/day change")


def get_conversation_id() -> str:
    global conversation_id, conversation_day
    with conversation_lock:
        if not conversation_id or conversation_day != today_string():
            conversation_id = str(uuid.uuid4())
            conversation_day = today_string()
            save_conversation_state()
            log.info("New conversation ID because of day change: %s", conversation_id)
        return conversation_id

load_conversation_state()

factory = ClientFactory(str(DB_PATH))
devices = factory.get_all_devices()
if devices:
    wa: NewClient = factory.new_client(jid=devices[0].JID)
    log.info("Stored WhatsApp session found.")
else:
    wa = factory.new_client(uuid="openassistant-whatsapp")
    log.info("No session found. Starting QR pairing.")


def jid_string(jid: Any) -> str:
    if jid is None:
        return ""
    try:
        return Jid2String(jid)
    except Exception:
        return str(jid)


def jid_user_digits(jid: Any) -> str:
    user = get_attr(jid, "User", "user")
    if user:
        return only_digits(str(user))
    return only_digits(jid_string(jid).split("@", 1)[0])


def source_candidates(source: Any) -> list[Any]:
    result = []
    sender = get_attr(source, "Sender", "sender")
    sender_alt = get_attr(source, "SenderAlt", "sender_alt", "senderAlt")
    if sender is not None:
        result.append(sender)
    if sender_alt is not None:
        try:
            if hasattr(sender_alt, "ListFields") and not sender_alt.ListFields():
                sender_alt = None
        except Exception:
            pass
    if sender_alt is not None:
        result.append(sender_alt)
    return result


def sender_is_allowed(source: Any) -> bool:
    return any(jid_user_digits(x) == ALLOWED_NUMBER for x in source_candidates(source))


def source_display(source: Any) -> str:
    vals = [jid_string(x) for x in source_candidates(source)]
    return " / ".join(v for v in vals if v) or "unknown"


def event_message(ev: Any) -> Any:
    return get_attr(ev, "message", "Message")


def event_info(ev: Any) -> Any:
    return get_attr(ev, "info", "Info")


def message_source(ev: Any) -> Any:
    return get_attr(event_info(ev), "message_source", "MessageSource")


def is_group_chat(chat: Any) -> bool:
    return "@g.us" in jid_string(chat)


def extract_text(ev: Any) -> Optional[str]:
    msg = event_message(ev)
    text = get_attr(msg, "conversation", "Conversation")
    if isinstance(text, str) and text.strip():
        return text.strip()
    ext = get_attr(msg, "extendedTextMessage", "extended_text_message", "ExtendedTextMessage")
    text = get_attr(ext, "text", "Text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def get_image_message(ev: Any) -> Any:
    image = get_attr(event_message(ev), "imageMessage", "image_message", "ImageMessage")
    if image is not None:
        try:
            if hasattr(image, "ListFields") and not image.ListFields():
                return None
        except Exception:
            pass
    return image


def extract_image_caption(image: Any) -> str:
    caption = get_attr(image, "caption", "Caption")
    return caption.strip() if isinstance(caption, str) else ""


def extract_image_mimetype(image: Any) -> str:
    mime = get_attr(image, "mimetype", "Mimetype", "mimeType")
    return mime if isinstance(mime, str) and mime.startswith("image/") else "image/jpeg"


def oa_headers(json_content: bool = True) -> dict[str, str]:
    headers = {"Accept": "*/*", "Cache-Control": "no-cache", "User-Agent": "whatsapp-openassistant-bridge/5.2"}
    if json_content:
        headers["Content-Type"] = "application/json"
    if OA_TOKEN:
        headers["Authorization"] = f"Bearer {OA_TOKEN}"
    return headers


def extract_text_from_json(data: Any) -> str:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    for key in ("delta", "content", "text", "response", "reply"):
        value = data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for sub in ("content", "text", "delta"):
                if isinstance(value.get(sub), str):
                    return value[sub]
    message = data.get("message")
    if isinstance(message, str):
        return message
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        first = choices[0]
        for container in (first.get("delta"), first.get("message")):
            if isinstance(container, dict) and isinstance(container.get("content"), str):
                return container["content"]
        if isinstance(first.get("text"), str):
            return first["text"]
    return ""


def extension_for_mimetype(mime: str) -> str:
    known = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic", "image/heif": ".heif"}
    return known.get(mime) or mimetypes.guess_extension(mime) or ".img"


def save_temp_image(binary: bytes, mime: str) -> Path:
    with tempfile.NamedTemporaryFile(mode="wb", suffix=extension_for_mimetype(mime), prefix="wa_", dir=TEMP_IMAGE_DIR, delete=False) as tmp:
        tmp.write(binary)
        path = Path(tmp.name)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def delete_temp_image(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
            log.info("Temporary image deleted: %s", path.name)
    except Exception:
        log.exception("Could not delete temporary image: %s", path)


def upload_image_to_openassistant(path: Path, mime: str) -> dict[str, Any]:
    with path.open("rb") as fh:
        response = requests.post(
            OA_UPLOAD_URL,
            files={"file": (path.name, fh, mime)},
            headers=oa_headers(json_content=False),
            timeout=REQUEST_TIMEOUT,
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("image_base64"), str) or not data["image_base64"]:
        raise RuntimeError("Open Assistant /api/upload did not return image_base64.")
    return data


def stream_openassistant(message: str, image_base64: Optional[str] = None) -> Optional[str]:
    payload: dict[str, Any] = {
        "message": message,
        "conversation_id": get_conversation_id(),
        "channel": OA_CHANNEL,
    }
    if image_base64:
        payload["image_base64"] = image_base64

    with requests.post(OA_URL, json=payload, headers=oa_headers(), timeout=REQUEST_TIMEOUT, stream=True) as response:
        response.raise_for_status()
        chunks: list[str] = []
        for raw_line in response.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith(("event:", "id:", "retry:")):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line in {"[DONE]", "DONE"}:
                if line in {"[DONE]", "DONE"}:
                    break
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                chunks.append(line)
                continue
            piece = extract_text_from_json(data)
            if piece:
                chunks.append(piece)
        reply = "".join(chunks).strip()
        return reply or None


def process_image_message(client: NewClient, ev: MessageEv, chat: Any, image: Any) -> None:
    temp_path: Optional[Path] = None
    try:
        caption = extract_image_caption(image)
        if caption.lower() == CLEAR_COMMAND:
            create_new_conversation("/clear")
            client.send_message(chat, "Context cleared. New chat started.")
            return

        mime = extract_image_mimetype(image)
        binary = client.download_any(event_message(ev))
        if not binary:
            raise RuntimeError("Could not download the WhatsApp image.")
        if len(binary) > MAX_IMAGE_BYTES:
            raise RuntimeError(f"Image is too large ({len(binary)/1024/1024:.1f} MB). Maximum is {MAX_IMAGE_MB} MB.")

        temp_path = save_temp_image(binary, mime)
        log.info("Image stored temporarily: %s (%d bytes)", temp_path.name, len(binary))
        del binary

        uploaded = upload_image_to_openassistant(temp_path, mime)
        image_base64 = uploaded["image_base64"]
        prompt = caption or DEFAULT_IMAGE_PROMPT

        # IMPORTANT:
        # Do NOT hand the technical file name (e.g. wa_abcd1234.jpg) to
        # Open Assistant as a semantically meaningful file name.
        # The image is visual context. References such as "the lower file",
        # "this one" or "this file" have to be resolved from the image
        # content before tools such as Nextcloud are used.
        oa_message = (
            "The user attached an image or screenshot as visual context. "
            "Analyse the image content first. "
            "If the user request refers to a visible element "
            "(e.g. 'the lower file', 'this file', 'this one', "
            "'the second entry' or similar), first identify the element "
            "that is actually meant based on the image. Then use the name, "
            "text or content visible in the image for any further tool "
            "calls, searches or actions. "
            "The technical name of the temporary image file is internal and "
            "must never be interpreted as a searched file name or as a user "
            "reference.\n\n"
            f"User request: {prompt}"
        )

        reply = stream_openassistant(
            oa_message,
            image_base64=image_base64,
        )
        del image_base64, uploaded
        if reply:
            client.send_message(chat, reply)
    finally:
        delete_temp_image(temp_path)


@wa.event(ConnectedEv)
def on_connected(client: NewClient, ev: ConnectedEv):
    print("\n✓ WhatsApp connected")
    print(f"✓ Allowed number: {ALLOWED_NUMBER}")
    print(f"✓ Open Assistant: {OA_URL}")
    print(f"✓ Upload API: {OA_UPLOAD_URL}")
    print(f"✓ Temporary images: {TEMP_IMAGE_DIR}")
    print("✓ Images are deleted locally after processing")
    print("✓ /clear = new chat, day change = new chat, Ctrl+C = quit\n")


@wa.event(MessageEv)
def on_message(client: NewClient, ev: MessageEv):
    source = message_source(ev)
    if source is None:
        return
    if bool(get_attr(source, "IsFromMe", "is_from_me", "isFromMe")):
        return
    chat = get_attr(source, "Chat", "chat")
    if chat is None:
        return
    if IGNORE_GROUPS and is_group_chat(chat):
        return
    if not sender_is_allowed(source):
        log.warning("Ignored message from a sender that is not allowed: %s", source_display(source))
        return

    image = get_image_message(ev)
    if image is not None:
        log.info("WhatsApp <- image | caption=%r | mime=%s", extract_image_caption(image), extract_image_mimetype(image))
        try:
            process_image_message(client, ev, chat, image)
        except requests.HTTPError as exc:
            body = exc.response.text[:1000] if exc.response is not None else ""
            log.error("Open Assistant image HTTP error: %s", body)
            try:
                client.send_message(chat, "The image could not be processed right now.")
            except Exception:
                pass
        except Exception as exc:
            log.exception("Error while processing an image")
            try:
                client.send_message(chat, f"The image could not be processed: {exc}")
            except Exception:
                pass
        return

    text = extract_text(ev)
    if not text:
        return
    if text.lower() == CLEAR_COMMAND:
        create_new_conversation("/clear")
        client.send_message(chat, "Context cleared. New chat started.")
        return

    try:
        reply = stream_openassistant(text)
        if reply:
            client.send_message(chat, reply)
    except requests.RequestException as exc:
        log.error("Open Assistant is not reachable: %s", exc)
    except Exception:
        log.exception("Error while processing a text message")


app = Flask(__name__)


def bridge_authorized() -> bool:
    if not BRIDGE_TOKEN:
        return HOST in {"127.0.0.1", "localhost", "::1"}
    return request.headers.get("Authorization", "") == f"Bearer {BRIDGE_TOKEN}"


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "conversation_id": get_conversation_id(),
        "conversation_day": conversation_day,
        "openassistant_url": OA_URL,
        "upload_url": OA_UPLOAD_URL,
        "temp_image_dir": str(TEMP_IMAGE_DIR),
    })


@app.post("/send")
def send_endpoint():
    if not bridge_authorized():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    recipient_raw = str(data.get("to") or data.get("recipient") or "").strip()
    text = str(data.get("message") or data.get("text") or "").strip()
    if not recipient_raw or not text:
        return jsonify({"error": "to/recipient and message/text are required"}), 400
    recipient_number = only_digits(recipient_raw.split("@", 1)[0] if "@" in recipient_raw else recipient_raw)
    if not ALLOW_SEND_TO_ANY and recipient_number != ALLOWED_NUMBER:
        return jsonify({"error": "recipient_not_allowed"}), 403
    try:
        wa.send_message(build_jid(recipient_number, "s.whatsapp.net"), text)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def run_http_server() -> None:
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)

shutdown_started = threading.Event()


def shutdown(signum: Optional[int] = None, frame: Any = None) -> None:
    if shutdown_started.is_set():
        return
    shutdown_started.set()
    print("\nShutting down the WhatsApp bridge ...")
    try:
        factory.stop()
    except Exception:
        log.exception("Error while stopping Neonize")
    print("✓ Stopped")

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

if __name__ == "__main__":
    print("=== WhatsApp <-> Open Assistant Bridge ===")
    print(f"Session DB:       {DB_PATH}")
    print(f"Conversation ID:  {get_conversation_id()}")
    print(f"Temp images:      {TEMP_IMAGE_DIR}")
    print("Image cleanup:    automatic")
    print("Ctrl+C:           quit\n")

    threading.Thread(target=run_http_server, daemon=True, name="reply-api").start()
    try:
        factory.run()
    except KeyboardInterrupt:
        shutdown()
    except Exception:
        log.exception("Bridge stopped because of an error")
        shutdown()
        raise
    finally:
        shutdown()
