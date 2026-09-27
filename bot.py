#!/usr/bin/env python3
"""
BDNET Config Decrypt Bot
========================
A Telegram bot that wraps bd_Dec.py.

- User taps a button (or sends /decrypt) -> the bot runs bd_Dec.py,
  which fetches the config over HTTPS and decrypts every field.
- The bot replies with a short summary AND the full JSON file.

Setup:
  1) Put this file (bot.py) in the SAME folder as bd_Dec.py.
  2) Set your bot token (from @BotFather) in the BOT_TOKEN env var,
     or paste it into the TOKEN line below.
  3) pip install requests
  4) python bot.py

For 24/7 cloud hosting the bot also starts a tiny health-check web
server on the port given by the PORT env var (used by hosts like
Render/Koyeb). Locally you can ignore it.
"""

import os
import sys
import glob
import json
import time
import html
import tempfile
import threading
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Prefer the environment variable (safer). Fall back to the pasted token.
TOKEN = os.environ.get("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE").strip()

# bd_Dec.py is expected next to this file.
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "bd_Dec.py")

API = f"https://api.telegram.org/bot{TOKEN}"

MAIN_KB = {
    "inline_keyboard": [
        [{"text": "🔓 Decrypt Config", "callback_data": "decrypt"}]
    ]
}

WELCOME = (
    "👋 <b>BDNET Config Decrypt Bot</b>\n\n"
    "নিচের বাটনে ক্লিক করলেই আমি config fetch করে decrypt করে দেব "
    "এবং তোমাকে সারাংশ + পুরো JSON ফাইল পাঠাবো।\n\n"
    "👇 শুরু করতে চাপো।"
)


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def api(method, **params):
    r = requests.post(f"{API}/{method}", data=params, timeout=60)
    return r.json()


def send_message(chat_id, text, reply_markup=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        params["reply_markup"] = json.dumps(reply_markup)
    return api("sendMessage", **params)


def send_document(chat_id, path, caption=None):
    with open(path, "rb") as f:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        r = requests.post(
            f"{API}/sendDocument", data=data, files={"document": f}, timeout=180
        )
    return r.json()


def answer_callback(cb_id, text=""):
    try:
        api("answerCallbackQuery", callback_query_id=cb_id, text=text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core: run bd_Dec.py and collect the result
# ---------------------------------------------------------------------------
def extract_report(text):
    """Pull the final balanced JSON object (the status report) out of stdout."""
    if not text:
        return None
    end = text.rfind("}")
    if end == -1:
        return None
    depth = 0
    for i in range(end, -1, -1):
        c = text[i]
        if c == "}":
            depth += 1
        elif c == "{":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:end + 1])
                except Exception:
                    return None
    return None


def run_decrypt():
    """Run bd_Dec.py into a temp dir. Return (exit_code, report_dict, json_path, raw_output)."""
    outdir = tempfile.mkdtemp(prefix="bdnet_")
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--output-dir", outdir],
        capture_output=True,
        text=True,
        timeout=180,
    )

    # The status report (status/version/servers/modes) is printed to stdout.
    report = extract_report(proc.stdout)

    # The generated JSON file holds the actual decrypted config -> sent to user.
    files = sorted(
        glob.glob(os.path.join(outdir, "*.json")),
        key=os.path.getmtime,
        reverse=True,
    )
    json_path = files[0] if files else None

    raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode, report, json_path, raw


def build_summary(report):
    m = report.get("modes", {}) or {}
    return (
        "✅ <b>Decryption সফল</b>\n\n"
        f"📦 Version: <b>{report.get('version', '?')}</b>\n"
        f"🖥 Servers: <b>{report.get('servers', '?')}</b>\n"
        f"🌐 Networks: <b>{report.get('networks', '?')}</b>\n"
        f"🔑 Decrypted fields: <b>{report.get('decrypted_fields', '?')}</b>\n"
        f"📊 Status: <b>{report.get('status', '?')}</b>\n"
        f"🧩 Modes: 0={m.get('0', 0)}, 1={m.get('1', 0)}, 2={m.get('2', 0)}"
    )


def summary_from_config(json_path):
    """Fallback summary built directly from the decrypted config file."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        version = cfg.get("Version", "?")
        servers = cfg.get("Servers", [])
        n_servers = len(servers) if isinstance(servers, list) else "?"
        return (
            "✅ <b>Decryption সফল</b>\n\n"
            f"📦 Version: <b>{version}</b>\n"
            f"🖥 Servers: <b>{n_servers}</b>\n"
            "📄 পুরো config নিচের JSON ফাইলে আছে।"
        )
    except Exception:
        return "✅ Config decrypt হয়েছে — বিস্তারিত নিচের JSON ফাইলে।"


def handle_decrypt(chat_id):
    send_message(chat_id, "⏳ Config fetch ও decrypt হচ্ছে, একটু অপেক্ষা করো...")
    try:
        code, report, json_path, raw = run_decrypt()
    except subprocess.TimeoutExpired:
        send_message(chat_id, "❌ সময় বেশি লাগছে (timeout)। ইন্টারনেট চেক করে আবার চেষ্টা করো।")
        return
    except FileNotFoundError:
        send_message(chat_id, "❌ bd_Dec.py খুঁজে পাওয়া যায়নি। bot.py আর bd_Dec.py একই ফোল্ডারে রাখো।")
        return
    except Exception as e:
        send_message(chat_id, f"❌ সমস্যা হয়েছে: <code>{html.escape(str(e))}</code>")
        return

    if report and report.get("status") == "fully_decrypted":
        send_message(chat_id, build_summary(report))
    elif report:
        send_message(
            chat_id,
            f"⚠️ আংশিক হয়েছে (exit {code}). Status: <b>{report.get('status')}</b>",
        )
    elif json_path and os.path.exists(json_path):
        # No report parsed, but the config file exists -> summarize from it.
        send_message(chat_id, summary_from_config(json_path))
    else:
        tail = html.escape((raw or "").strip()[-1200:]) or "কোনো output নেই।"
        send_message(chat_id, f"❌ Decrypt হয়নি (exit {code}).\n\n<pre>{tail}</pre>")

    if json_path and os.path.exists(json_path):
        send_document(chat_id, json_path, caption="📄 তোমার recover করা config")


# ---------------------------------------------------------------------------
# Health-check server (only needed for cloud hosts that require an open port)
# ---------------------------------------------------------------------------
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"BDNET bot is running.")

    def log_message(self, *args):
        pass


def start_health_server():
    port = os.environ.get("PORT")
    if not port:
        return
    try:
        srv = HTTPServer(("0.0.0.0", int(port)), _Health)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"Health server on port {port}")
    except Exception as e:
        print("Health server failed:", e)


# ---------------------------------------------------------------------------
# Main long-polling loop
# ---------------------------------------------------------------------------
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("ERROR: Bot token সেট করা হয়নি। BOT_TOKEN env var দাও অথবা কোডে বসাও।")
        sys.exit(1)
    if not os.path.exists(SCRIPT):
        print(f"ERROR: bd_Dec.py পাওয়া যায়নি: {SCRIPT}")
        sys.exit(1)

    start_health_server()

    print("Bot started. Waiting for messages...")
    offset = None
    while True:
        try:
            res = api("getUpdates", offset=offset, timeout=30)
        except Exception as e:
            print("getUpdates error:", e)
            time.sleep(3)
            continue

        if not res.get("ok"):
            time.sleep(3)
            continue

        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            try:
                if "message" in upd:
                    msg = upd["message"]
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text", "") or ""
                    if text.startswith("/start"):
                        send_message(chat_id, WELCOME, MAIN_KB)
                    elif text.startswith("/decrypt"):
                        handle_decrypt(chat_id)
                    else:
                        send_message(chat_id, "নিচের বাটন চাপো 👇", MAIN_KB)

                elif "callback_query" in upd:
                    cb = upd["callback_query"]
                    chat_id = cb["message"]["chat"]["id"]
                    answer_callback(cb["id"], "কাজ শুরু হয়েছে...")
                    if cb.get("data") == "decrypt":
                        handle_decrypt(chat_id)
            except Exception as e:
                print("update handling error:", e)


if __name__ == "__main__":
    main()
