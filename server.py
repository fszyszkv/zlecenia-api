"""
🌐 Serwer API dla Nadawcy Zleceń — wersja dla Render (PostgreSQL)
"""

import json
import os
from datetime import datetime

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, request, jsonify


# ═══════════════════════════════════════════════════════════
#  🔧 UZUPEŁNIJ PONIŻEJ — 3 WARTOŚCI
# ═══════════════════════════════════════════════════════════

# 1) Token od @BotFather (ten sam co w main.py)
BOT_TOKEN_DEFAULT = "8805309179:AAFgKKy5KOwYu3KSTysJagkdnu5ir9liXmY"

# 2) Connection string z Neon.tech (patrz KROK 3)
DATABASE_URL_DEFAULT = "postgresql://neondb_owner:npg_LIRp8qMU1NzB@ep-aged-bonus-b4b9409a-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# 3) Twój tajny klucz API — wymyśl dowolny długi ciąg znaków
API_KEY_DEFAULT = "4918-8544-5214-22"

# ═══════════════════════════════════════════════════════════


# Render nadpisze te wartości ze zmiennych środowiskowych
BOT_TOKEN = os.environ.get("BOT_TOKEN", BOT_TOKEN_DEFAULT)
DATABASE_URL = os.environ.get("DATABASE_URL", DATABASE_URL_DEFAULT)
API_KEY = os.environ.get("API_KEY", API_KEY_DEFAULT)


app = Flask(__name__)


def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE,
                    identifier TEXT UNIQUE,
                    eth_address TEXT,
                    balance REAL DEFAULT 0,
                    created_at TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    content TEXT,
                    claimed_by INTEGER,
                    status TEXT DEFAULT 'pending',
                    screenshot_path TEXT,
                    completed_at TIMESTAMP,
                    approved INTEGER DEFAULT 0,
                    created_at TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS user_messages (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    task_id INTEGER,
                    message_id BIGINT,
                    chat_id BIGINT
                );
            """)


def _check_key():
    return request.headers.get("X-API-Key") == API_KEY


def _send_tg(chat_id, text, task_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "reply_markup": json.dumps({
            "inline_keyboard": [[
                {"text": "✅ CLAIM", "callback_data": f"claim:{task_id}"}
            ]]
        }),
    }, timeout=15)
    r.raise_for_status()
    return r.json()["result"]["message_id"]


@app.route("/ping")
def ping():
    return jsonify({"ok": True, "service": "task-sender"})


@app.route("/send", methods=["POST"])
def send():
    if not _check_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(force=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "empty content"}), 400

    with db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO tasks (content, created_at) VALUES (%s, %s) RETURNING id",
                (content, datetime.now()),
            )
            task_id = cur.fetchone()["id"]

            cur.execute(
                "SELECT * FROM users WHERE telegram_id IS NOT NULL AND eth_address IS NOT NULL"
            )
            users = cur.fetchall()

            sent = 0
            for u in users:
                try:
                    mid = _send_tg(u["telegram_id"], f"🆕 Nowe Zlecenie: {content}", task_id)
                    cur.execute(
                        "INSERT INTO user_messages (user_id, task_id, message_id, chat_id) "
                        "VALUES (%s, %s, %s, %s)",
                        (u["id"], task_id, mid, u["telegram_id"]),
                    )
                    sent += 1
                except Exception as e:
                    print(f"[TG ERR] {u['identifier']}: {e}")

            conn.commit()
            return jsonify({"ok": True, "task_id": task_id, "sent": sent, "total": len(users)})


try:
    init_db()
    print("[OK] Baza zainicjalizowana.")
except Exception as e:
    print(f"[DB INIT ERR] {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)