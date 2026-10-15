"""
🌐 Serwer API + Bot Telegram (Render) — WEBHOOK + lazy start w workerze
"""

import asyncio
import os
import re
import threading
import time
from datetime import datetime

import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)


# ═══════════════════════════════════════════════════════════
#  🔧 UZUPEŁNIJ PONIŻEJ — 6 WARTOŚCI
# ═══════════════════════════════════════════════════════════

# 1) Token od @BotFather (ten sam co w main.py)
BOT_TOKEN_DEFAULT = "8805309179:AAFgKKy5KOwYu3KSTysJagkdnu5ir9liXmY"

# 2) Connection string z Neon.tech (patrz KROK 3)
DATABASE_URL_DEFAULT = "postgresql://neondb_owner:npg_LIRp8qMU1NzB@ep-aged-bonus-b4b9409a-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# 3) Twój tajny klucz API — wymyśl dowolny długi ciąg znaków
API_KEY_DEFAULT = "4918-8544-5214-22"

# Sekret webhooka — wymyśl losowy ciąg np. "x9k2m4p7q1" (bez znaków specjalnych)
WEBHOOK_SECRET_DEFAULT = "9574246-653453"

# Publiczny URL Twojego serwisu Render (bez / na końcu)
WEBHOOK_BASE_URL_DEFAULT = "https://zlecenia-api.onrender.com"

# ═══════════════════════════════════════════════════════════


BOT_TOKEN = os.environ.get("BOT_TOKEN", BOT_TOKEN_DEFAULT)
DATABASE_URL = os.environ.get("DATABASE_URL", DATABASE_URL_DEFAULT)
API_KEY = os.environ.get("API_KEY", API_KEY_DEFAULT)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", WEBHOOK_SECRET_DEFAULT)
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL", WEBHOOK_BASE_URL_DEFAULT)

SCREENSHOTS_DIR = "screenshots"
IDENTIFIER_REGEX = r"^[0-9A-Za-z]{5}$"

app = Flask(__name__)


# ═══════════════════════════════════════════════════════════
#  BAZA
# ═══════════════════════════════════════════════════════════

def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def db_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


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


def db_get_user_by_tid(tid):
    with db() as conn:
        with db_cursor(conn) as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id = %s", (tid,))
            return cur.fetchone()


def db_get_user_by_identifier(ident):
    with db() as conn:
        with db_cursor(conn) as cur:
            cur.execute("SELECT * FROM users WHERE identifier = %s", (ident,))
            return cur.fetchone()


def db_get_user_by_id(uid):
    with db() as conn:
        with db_cursor(conn) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (uid,))
            return cur.fetchone()


def db_get_task(tid):
    with db() as conn:
        with db_cursor(conn) as cur:
            cur.execute("SELECT * FROM tasks WHERE id = %s", (tid,))
            return cur.fetchone()


def db_get_active_users():
    with db() as conn:
        with db_cursor(conn) as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id IS NOT NULL AND eth_address IS NOT NULL")
            return cur.fetchall()


def db_link_user(identifier, telegram_id, eth):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET telegram_id = %s, eth_address = %s WHERE identifier = %s",
                (telegram_id, eth, identifier),
            )


def db_update_user_eth(tid, eth):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET eth_address = %s WHERE telegram_id = %s", (eth, tid))


def db_create_task(content):
    with db() as conn:
        with db_cursor(conn) as cur:
            cur.execute(
                "INSERT INTO tasks (content, created_at) VALUES (%s, %s) RETURNING id",
                (content, datetime.now()),
            )
            return cur.fetchone()["id"]


def db_claim_task(task_id, user_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET claimed_by = %s, status = 'claimed' "
                "WHERE id = %s AND claimed_by IS NULL",
                (user_id, task_id),
            )
            return cur.rowcount > 0


def db_get_active_claim(user_id):
    with db() as conn:
        with db_cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM tasks WHERE claimed_by = %s AND "
                "(status = 'claimed' OR (status = 'completed' AND approved = 0))",
                (user_id,),
            )
            return cur.fetchone()


def db_complete_task(task_id, screenshot_path):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET status = 'completed', screenshot_path = %s, "
                "completed_at = %s WHERE id = %s",
                (screenshot_path, datetime.now(), task_id),
            )


def db_save_user_message(user_id, task_id, message_id, chat_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_messages (user_id, task_id, message_id, chat_id) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, task_id, message_id, chat_id),
            )


def db_get_user_messages(task_id):
    with db() as conn:
        with db_cursor(conn) as cur:
            cur.execute("SELECT * FROM user_messages WHERE task_id = %s", (task_id,))
            return cur.fetchall()


# ═══════════════════════════════════════════════════════════
#  BOT (WEBHOOK)
# ═══════════════════════════════════════════════════════════

class BotRunner:
    def __init__(self, token):
        self.token = token
        self.loop = None
        self.app = None
        self.pending = {}
        self.started = False
        self.failed = False
        self._thread = None

    def start_in_thread(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        print("[BOT] _run() start", flush=True)
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._async_main())
        except Exception as e:
            self.failed = True
            print(f"[BOT] FATAL: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()

    async def _async_main(self):
        print("[BOT] 1/5 Buduję Application...", flush=True)
        self.app = Application.builder().token(self.token).build()

        print("[BOT] 2/5 Rejestruję handlery...", flush=True)
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CallbackQueryHandler(self.on_callback))
        self.app.add_handler(MessageHandler(filters.PHOTO, self.on_photo))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        self.app.add_error_handler(self._on_error)

        print("[BOT] 3/5 initialize()...", flush=True)
        await self.app.initialize()

        print("[BOT] 4/5 start()...", flush=True)
        await self.app.start()

        webhook_url = f"{WEBHOOK_BASE_URL}/webhook/{WEBHOOK_SECRET}"
        print(f"[BOT] 5/5 set_webhook → {webhook_url}", flush=True)
        await self.app.bot.set_webhook(
            url=webhook_url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )

        print("[BOT] ✅ Webhook ustawiony — bot gotowy.", flush=True)
        self.started = True
        self._stop_event = asyncio.Event()
        await self._stop_event.wait()

    def process_update_sync(self, update: Update):
        if not self.loop:
            return False
        fut = asyncio.run_coroutine_threadsafe(
            self.app.process_update(update), self.loop
        )
        try:
            fut.result(timeout=30)
            return True
        except Exception as e:
            print(f"[BOT] process_update error: {e}", flush=True)
            return False

    async def _on_error(self, update, context):
        err = context.error
        print(f"[BOT] handler error: {type(err).__name__}: {err}", flush=True)
        try:
            if update and update.callback_query:
                await update.callback_query.answer()
        except Exception:
            pass

    # ---------- HANDLERY ----------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tid = update.effective_user.id
        user = db_get_user_by_tid(tid)
        if user and user["eth_address"]:
            await update.message.reply_text("✅ Masz już pełny dostęp do bota.")
            return
        if user:
            self.pending[tid] = {"state": "awaiting_eth"}
            await update.message.reply_text("Podaj swój adres Ethereum (0x...):")
            return
        self.pending[tid] = {"state": "awaiting_id"}
        await update.message.reply_text(
            "👋 Witaj!\nPodaj swój 5-znakowy identyfikator (format np. `00a01`):",
            parse_mode="Markdown",
        )

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tid = update.effective_user.id
        text = (update.message.text or "").strip()
        if tid not in self.pending:
            return
        state = self.pending[tid].get("state")

        if state == "awaiting_id":
            if not re.match(IDENTIFIER_REGEX, text):
                await update.message.reply_text(
                    "❌ Nieprawidłowy format. Wymagany: 5 znaków alfanumerycznych (np. `00a01`).",
                    parse_mode="Markdown",
                )
                return
            acc = db_get_user_by_identifier(text)
            if not acc:
                await update.message.reply_text("❌ Nieznany identyfikator.")
                return
            if acc["telegram_id"] and acc["telegram_id"] != tid:
                await update.message.reply_text("❌ Ten identyfikator jest już przypisany.")
                return
            self.pending[tid] = {"state": "awaiting_eth", "identifier": text}
            await update.message.reply_text("✅ Identyfikator OK! Teraz podaj adres Ethereum (0x..., 42 znaki):")

        elif state == "awaiting_eth":
            if not (text.startswith("0x") and len(text) == 42):
                await update.message.reply_text(
                    "❌ Adres musi zaczynać się od `0x` i mieć 42 znaki.",
                    parse_mode="Markdown",
                )
                return
            ident = self.pending[tid]["identifier"]
            existing = db_get_user_by_tid(tid)
            if existing:
                db_update_user_eth(tid, text)
            else:
                db_link_user(ident, tid, text)
            self.pending.pop(tid, None)
            await update.message.reply_text("🎉 Konto aktywowane! Otrzymasz zlecenia, gdy tylko się pojawią.")

        elif state == "awaiting_screenshot":
            await update.message.reply_text("📸 Wyślij zrzut ekranu (zdjęcie), nie tekst.")

    async def on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        tid = update.effective_user.id
        if tid not in self.pending or self.pending[tid].get("state") != "awaiting_screenshot":
            return

        task_id = self.pending[tid]["task_id"]
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()

        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOTS_DIR, f"task_{task_id}_{tid}.jpg")
        await tg_file.download_to_drive(path)

        self.pending[tid] = {
            "state": "awaiting_confirm",
            "task_id": task_id,
            "screenshot_path": path,
        }
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Tak", callback_data=f"cyes:{task_id}"),
            InlineKeyboardButton("❌ Nie", callback_data=f"cno:{task_id}"),
        ]])
        await update.message.reply_text("Czy potwierdzasz wykonanie tego zlecenia?", reply_markup=kb)

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        data = q.data or ""
        tid = q.from_user.id

        try:
            await q.answer()
        except Exception as e:
            print(f"[TG] answer initial failed: {e}", flush=True)

        user = db_get_user_by_tid(tid)
        if not user:
            await q.message.reply_text("Najpierw /start")
            return

        if data.startswith("claim:"):
            task_id = int(data.split(":")[1])
            task = db_get_task(task_id)
            if not task or task["claimed_by"] is not None:
                try:
                    await q.answer("❌ Ktoś już to przejął.", show_alert=True)
                except Exception:
                    pass
                try:
                    await q.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                return
            if db_get_active_claim(user["id"]):
                try:
                    await q.answer("❌ Masz już aktywne zlecenie.", show_alert=True)
                except Exception:
                    pass
                return
            if not db_claim_task(task_id, user["id"]):
                try:
                    await q.answer("❌ Spóźniłeś się.", show_alert=True)
                except Exception:
                    pass
                return

            new_text = (q.message.text or "") + "\n\n✅ Przejęte przez Ciebie"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 Oznacz jako zrealizowane", callback_data=f"complete:{task_id}")
            ]])
            edited = False
            try:
                await q.edit_message_text(text=new_text, reply_markup=kb)
                edited = True
            except Exception as e:
                print(f"[BOT] edit failed: {type(e).__name__}: {e}", flush=True)
            if not edited:
                try:
                    await q.message.reply_text(
                        f"✅ Przejąłeś zlecenie #{task_id}.\nKliknij poniżej, gdy je wykonasz.",
                        reply_markup=kb,
                    )
                except Exception as e:
                    print(f"[BOT] reply fallback failed: {e}", flush=True)
            await self._clear_claim_buttons(task_id, user["id"])

        elif data.startswith("complete:"):
            task_id = int(data.split(":")[1])
            task = db_get_task(task_id)
            if not task or task["claimed_by"] != user["id"]:
                try:
                    await q.answer("❌ Nie jesteś właścicielem.", show_alert=True)
                except Exception:
                    pass
                return
            self.pending[tid] = {"state": "awaiting_screenshot", "task_id": task_id}
            await q.message.reply_text("📸 Wyślij zrzut ekranu z balansu gwiazdek Telegram:")

        elif data.startswith("cyes:"):
            task_id = int(data.split(":")[1])
            info = self.pending.get(tid)
            if not info or info.get("task_id") != task_id or info.get("state") != "awaiting_confirm":
                try:
                    await q.answer("Brak kontekstu.", show_alert=True)
                except Exception:
                    pass
                return
            db_complete_task(task_id, info["screenshot_path"])
            self.pending.pop(tid, None)
            try:
                await q.edit_message_text("✅ Zgłoszenie wysłane do administratora.")
            except Exception:
                pass

        elif data.startswith("cno:"):
            task_id = int(data.split(":")[1])
            self.pending[tid] = {"state": "awaiting_screenshot", "task_id": task_id}
            try:
                await q.edit_message_text("OK — wyślij ponownie zrzut ekranu:")
            except Exception:
                pass

    async def _clear_claim_buttons(self, task_id, claimed_user_id):
        task = db_get_task(task_id)
        messages = db_get_user_messages(task_id)
        claimed_user = db_get_user_by_id(task["claimed_by"])
        for m in messages:
            if claimed_user and m["chat_id"] == claimed_user["telegram_id"]:
                continue
            try:
                await self.app.bot.edit_message_reply_markup(
                    chat_id=m["chat_id"], message_id=m["message_id"], reply_markup=None
                )
            except Exception:
                pass

    def send_task_to_all(self, content):
        if not self.loop or not self.started:
            return 0
        fut = asyncio.run_coroutine_threadsafe(self._send_task(content), self.loop)
        try:
            return fut.result(timeout=60)
        except Exception as e:
            print(f"[BOT] send_task error: {e}", flush=True)
            return 0

    async def _send_task(self, content):
        users = db_get_active_users()
        if not users:
            return 0
        task_id = db_create_task(content)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ CLAIM", callback_data=f"claim:{task_id}")
        ]])
        sent = 0
        for u in users:
            try:
                msg = await self.app.bot.send_message(
                    chat_id=u["telegram_id"],
                    text=f"🆕 Nowe Zlecenie: {content}",
                    reply_markup=kb,
                )
                db_save_user_message(u["id"], task_id, msg.message_id, u["telegram_id"])
                sent += 1
            except Exception as e:
                print(f"[BOT] nie wysłano do {u['telegram_id']}: {e}", flush=True)
        return sent

    def notify_one(self, telegram_id, text):
        if not self.loop or not self.started:
            return False
        fut = asyncio.run_coroutine_threadsafe(
            self.app.bot.send_message(chat_id=telegram_id, text=text), self.loop
        )
        try:
            fut.result(timeout=15)
            return True
        except Exception as e:
            print(f"[BOT] notify error: {e}", flush=True)
            return False


# ═══════════════════════════════════════════════════════════
#  API
# ═══════════════════════════════════════════════════════════

bot = BotRunner(BOT_TOKEN)
_bot_lock = threading.Lock()


def ensure_bot_running():
    """Uruchamia bota leniwie — w tym samym procesie, który obsługuje żądania."""
    if bot.started or bot.failed:
        return
    with _bot_lock:
        if bot.started or bot.failed:
            return
        print("[BOT] Lazy start (in worker process)...", flush=True)
        bot.start_in_thread()
        for _ in range(200):   # max 20s
            if bot.started or bot.failed:
                break
            time.sleep(0.1)
        print(f"[BOT] Lazy start done. started={bot.started}, failed={bot.failed}", flush=True)


def _check_key():
    return request.headers.get("X-API-Key") == API_KEY


@app.route("/ping")
def ping():
    ensure_bot_running()
    return jsonify({"ok": True, "service": "task-sender", "bot_started": bot.started})


@app.route("/send", methods=["POST"])
def send():
    if not _check_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    ensure_bot_running()

    data = request.get_json(force=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "empty content"}), 400

    if not bot.started:
        return jsonify({"ok": False, "error": "bot not ready"}), 503

    sent = bot.send_task_to_all(content)
    return jsonify({"ok": True, "sent": sent})


@app.route("/notify", methods=["POST"])
def notify():
    if not _check_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    ensure_bot_running()

    data = request.get_json(force=True) or {}
    tid = data.get("telegram_id")
    text = data.get("text")
    if not tid or not text:
        return jsonify({"ok": False, "error": "missing fields"}), 400

    if not bot.started:
        return jsonify({"ok": False, "error": "bot not ready"}), 503

    ok = bot.notify_one(tid, text)
    return jsonify({"ok": ok})


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return "forbidden", 403

    ensure_bot_running()

    try:
        data = request.get_json(force=True)
        if not bot.app:
            return "not ready", 503
        update = Update.de_json(data, bot.app.bot)
        bot.process_update_sync(update)
    except Exception as e:
        print(f"[WEBHOOK ERR] {e}", flush=True)
    return "ok", 200


# ═══════════════════════════════════════════════════════════
#  START (nic nie robimy tu — bot startuje leniwie)
# ═══════════════════════════════════════════════════════════

print("=== START SERVER.PY (WEBHOOK) ===", flush=True)

try:
    init_db()
    print("[OK] Baza zainicjalizowana.", flush=True)
except Exception as e:
    print(f"[DB INIT ERR] {e}", flush=True)

print(f"[..] BOT_TOKEN długość: {len(BOT_TOKEN)}", flush=True)
print(f"[..] WEBHOOK_BASE_URL: {WEBHOOK_BASE_URL}", flush=True)
print(f"[..] WEBHOOK path: /webhook/{WEBHOOK_SECRET}", flush=True)
print("[..] Bot uruchomi się leniwie przy pierwszym żądaniu.", flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)