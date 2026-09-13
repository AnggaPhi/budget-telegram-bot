"""
Budget Bot — Telegram Webhook Handler (Vercel-safe)

CRITICAL: Only Flask and stdlib are imported at module level.
All telegram & services imports happen inside functions to prevent
Vercel cold-start crashes.
"""

import os
import sys
import json
import logging
import asyncio
import traceback
import re
from datetime import datetime

# ── Fix import path so Vercel can find the services/ folder ──────────────────
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from flask import Flask, request, Response

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

CATEGORIES = [
    "Food & Drinks", "Groceries", "Transport", "Shopping",
    "Bills & Utilities", "Entertainment", "Health",
    "Personal Care", "Education", "Others",
]

# ── Flask App (this is what Vercel detects as the WSGI interface) ─────────────
app = Flask(__name__)

# ── Lazy Telegram Application (only built on first real request) ──────────────
_bot_app = None


def get_bot_app():
    """Build the Telegram Application once per cold start."""
    global _bot_app
    if _bot_app is None:
        from telegram.ext import (
            Application, CommandHandler, MessageHandler,
            CallbackQueryHandler, filters,
        )
        if not BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
        _bot_app = (
            Application.builder()
            .token(BOT_TOKEN)
            .updater(None)
            .build()
        )
        _bot_app.add_handler(CommandHandler("start", cmd_start))
        _bot_app.add_handler(CommandHandler("help", cmd_help))
        _bot_app.add_handler(CommandHandler("summary", cmd_summary))
        _bot_app.add_handler(CommandHandler("balance", cmd_balance))
        _bot_app.add_handler(CommandHandler("sisa", cmd_balance))
        _bot_app.add_handler(CommandHandler("credit", cmd_credit))
        _bot_app.add_handler(CommandHandler("debt", cmd_credit))
        _bot_app.add_handler(CommandHandler("hutang", cmd_credit))
        _bot_app.add_handler(CommandHandler("link", cmd_link))
        _bot_app.add_handler(CommandHandler("sheet", cmd_link))
        _bot_app.add_handler(CommandHandler("spreadsheet", cmd_link))
        _bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        _bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        _bot_app.add_handler(CallbackQueryHandler(handle_callback))
        logger.info("Telegram Application built OK.")
    return _bot_app


async def _process_update(payload):
    """Run update inside proper application async lifecycle context."""
    from telegram import Update
    bot_app = get_bot_app()
    async with bot_app:
        update = Update.de_json(payload, bot_app.bot)
        await bot_app.process_update(update)



# ── Session store (ephemeral, resets on cold start) ───────────────────────────
sessions = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_amount(amount):
    try:
        amt = int(amount)
        if amt < 0:
            return f"-Rp {abs(amt):,}".replace(",", ".")
        return f"Rp {amt:,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(amount)


def parse_amount(val) -> int:
    if val is None:
        return 0
    s = str(val).replace("Rp", "").replace("IDR", "").strip()
    s = re.sub(r'[,.]00$', '', s)
    digits = re.sub(r'[^\d]', '', s)
    return int(digits) if digits else 0



def format_confirmation(data):
    tx_type = (data.get("type") or "expense").lower()
    emoji_map = {"expense": "💸", "income": "💰", "debt": "🔴", "credit": "🟢", "asset": "📈"}
    emoji = emoji_map.get(tx_type, "💸")
    lines = [
        f"{emoji} *{tx_type.capitalize()} Transaction*\n",
        f"📅 Date: `{data.get('date') or 'Unknown'}`",
        f"🏪 Merchant/Source: `{data.get('merchant') or 'Unknown'}`",
        f"💰 Amount: `{format_amount(data.get('amount', 0))}`",
    ]
    if data.get("category"):
        lines.append(f"🗂️ Category: `{data['category']}`")
    if data.get("notes"):
        lines.append(f"📝 Notes: `{data['notes']}`")
    conf = (data.get("confidence") or "medium").lower()
    conf_icon = {"high": "✅", "medium": "⚠️", "low": "❌"}.get(conf, "⚠️")
    lines.append(f"\n{conf_icon} AI Confidence: *{conf.upper()}*")
    lines.append("\nIs this correct?")
    return "\n".join(lines)


def kb_confirm():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Save", callback_data="confirm_save"),
        InlineKeyboardButton("✏️ Edit", callback_data="confirm_edit"),
        InlineKeyboardButton("❌ Cancel", callback_data="confirm_cancel"),
    ]])


def kb_edit():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Date", callback_data="edit_date"),
         InlineKeyboardButton("🏪 Merchant", callback_data="edit_merchant")],
        [InlineKeyboardButton("💰 Amount", callback_data="edit_amount"),
         InlineKeyboardButton("🗂️ Category", callback_data="edit_category")],
        [InlineKeyboardButton("📝 Notes", callback_data="edit_notes"),
         InlineKeyboardButton("🔄 Type", callback_data="edit_type")],
        [InlineKeyboardButton("« Back", callback_data="edit_back")],
    ])


def kb_category():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    buttons, row = [], []
    for cat in CATEGORIES:
        row.append(InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("« Back", callback_data="confirm_edit")])
    return InlineKeyboardMarkup(buttons)


def kb_type():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    types = [
        ("💸 Expense", "type_expense"), ("💰 Income", "type_income"),
        ("🔴 Debt", "type_debt"), ("🟢 Credit", "type_credit"),
        ("📈 Asset", "type_asset"),
    ]
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(lbl, callback_data=cb)] for lbl, cb in types]
        + [[InlineKeyboardButton("« Back", callback_data="confirm_edit")]]
    )


# ── Command Handlers ──────────────────────────────────────────────────────────

async def cmd_start(update, context):
    await update.message.reply_text(
        "👋 *Selamat datang di Budget Bot!*\n\n"
        "Kirim satu dari ini:\n"
        "📸 *Foto struk* → Bot baca otomatis dengan AI\n"
        "💬 *Teks bebas* → contoh: `Beli makan siang 25000`\n\n"
        "📊 /summary — Ringkasan bulan ini\n"
        "💰 /balance — Sisa uang (Income - Expenses)\n"
        "💳 /credit — Cek tagihan hutang & cicilan\n"
        "🔗 /link — Link langsung ke Google Spreadsheet\n"
        "❓ /help — Bantuan lengkap",
        parse_mode="Markdown",
    )


async def cmd_help(update, context):
    await update.message.reply_text(
        "🤖 *Budget Bot — Panduan*\n\n"
        "*📸 Foto Struk:*\nKirim foto langsung → AI baca otomatis\n\n"
        "*💬 Contoh Teks:*\n"
        "• `Beli bensin Shell 80000`\n"
        "• `Bayar listrik PLN 450000`\n"
        "• `Gaji bulan ini 8000000`\n"
        "• `Pinjem ke Budi 150000 buat makan`\n\n"
        "*Commands:*\n"
        "/summary — Ringkasan transaksi & keuangan bulan ini\n"
        "/balance — Cek sisa uang (Income I25 - Expenses I10)\n"
        "/credit — Cek tagihan hutang & cicilan (contoh: `/credit`, `/credit oct`, `/credit depan`)\n"
        "/link — Buka link langsung Google Spreadsheet\n"
        "/start — Mulai ulang\n"
        "/help — Panduan ini",
        parse_mode="Markdown",
    )


async def cmd_summary(update, context):
    from services.sheets_service import get_monthly_summary
    await update.message.reply_text("⏳ Mengambil data...")
    result = get_monthly_summary()
    if not result["success"]:
        await update.message.reply_text(f"❌ Gagal: {result['error']}")
        return

    txs = result["transactions"]
    month_name = datetime.now().strftime("%B %Y")
    if not txs:
        await update.message.reply_text(
            f"📊 *{month_name}*\n\nBelum ada transaksi.", parse_mode="Markdown"
        )
        return

    cat_totals = {}
    grand_total = 0
    for tx in txs:
        amt = parse_amount(tx.get("amount", 0))
        cat = str(tx.get("category") or "").strip() or "Others"
        cat_totals[cat] = cat_totals.get(cat, 0) + amt
        grand_total += amt

    lines = [f"📊 *Ringkasan {month_name}*\n"]
    for cat, total in sorted(cat_totals.items(), key=lambda x: -x[1]):
        lines.append(f"  {cat}: `{format_amount(total)}`")
    lines += [f"\n💸 *Total Pengeluaran (Tabel):* `{format_amount(grand_total)}`"]
    max_tx = result.get("max_transactions", 30)
    if len(txs) >= max_tx:
        lines.append(f"⚠️ *{len(txs)}/{max_tx} transaksi (Penuh!)*")
    else:
        lines.append(f"📋 {len(txs)}/{max_tx} transaksi")

    # Financial balance from Sheet cells: Income (I25) - Total Expenses (I10)
    income = result.get("income", 0)
    expenses = result.get("expenses", 0)
    money_left = result.get("money_left", 0)
    money_icon = "💰" if money_left >= 0 else "🚨"

    lines += [
        "\n━━━━━━━━━━━━━━━━━━",
        f"💵 *Income (I25):* `{format_amount(income)}`",
        f"📉 *Expenses (I10):* `{format_amount(expenses)}`",
        f"{money_icon} *Sisa Uang (Money Left):* `{format_amount(money_left)}`",
    ]
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from services.sheets_service import SHEET_ID
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Buka Sheet", url=sheet_url),
            InlineKeyboardButton("❌ Tutup", callback_data="close_message"),
        ]
    ])
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)


async def cmd_balance(update, context):
    from services.sheets_service import get_money_left, SHEET_ID
    await update.message.reply_text("⏳ Menghitung sisa uang...")
    result = get_money_left()
    if not result.get("success"):
        await update.message.reply_text(f"❌ Gagal: {result.get('error')}")
        return

    income = result.get("income", 0)
    expenses = result.get("expenses", 0)
    money_left = result.get("money_left", 0)
    month_name = datetime.now().strftime("%B %Y")
    money_icon = "💰" if money_left >= 0 else "🚨"

    msg = (
        f"💵 *Status Keuangan — {month_name}*\n\n"
        f"📥 *Total Income (I25):* `{format_amount(income)}`\n"
        f"📤 *Total Expenses (I10):* `{format_amount(expenses)}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{money_icon} *Sisa Uang (Money Left):* `{format_amount(money_left)}`"
    )
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Buka Sheet", url=sheet_url),
            InlineKeyboardButton("❌ Tutup", callback_data="close_message"),
        ]
    ])
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)


def kb_credit_nav(month: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from services.sheets_service import MONTH_NAME_MAP_ID, SHEET_ID
    prev_m = 12 if month == 1 else month - 1
    next_m = 1 if month == 12 else month + 1
    prev_name = MONTH_NAME_MAP_ID.get(prev_m, str(prev_m))[:3]
    next_name = MONTH_NAME_MAP_ID.get(next_m, str(next_m))[:3]
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"◀️ {prev_name}", callback_data=f"credit_nav_{prev_m}"),
            InlineKeyboardButton(f"{next_name} ▶️", callback_data=f"credit_nav_{next_m}"),
        ],
        [
            InlineKeyboardButton("📊 Buka Sheet", url=sheet_url),
            InlineKeyboardButton("❌ Tutup", callback_data="close_message"),
        ]
    ])


def format_credit_message(result: dict) -> str:
    if not result.get("success"):
        err = result.get("error", "Terjadi kesalahan.")
        return f"❌ Gagal mengambil data hutang & cicilan:\n`{err}`"

    month_name = result.get("month_name", "Bulan ini")
    year = datetime.now().year
    items = result.get("items", [])
    total = result.get("total", 0)

    lines = [
        f"💳 *Daftar Tagihan Hutang & Cicilan*",
        f"📅 Periode: *{month_name} {year}*",
        "━━━━━━━━━━━━━━━━━━",
    ]

    unpaid = [it for it in items if it["amount"] > 0]
    paid = [it for it in items if it["amount"] == 0]

    if not items:
        lines.append("ℹ️ _Tidak ada data hutang/cicilan pada sheet bulan ini._")
    else:
        if unpaid:
            for it in unpaid:
                due = f" *(Jatuh tempo: {it['due_date']})*" if it.get("due_date") else ""
                lines.append(f"🔴 *{it['label']}*: `{format_amount(it['amount'])}`{due}")
        else:
            lines.append("🎉 *Semua tagihan lunas untuk bulan ini!*")

        if paid:
            lines.append("\n*Sudah Lunas / Rp 0:*")
            for it in paid:
                lines.append(f"  🟢 {it['label']}: `Rp 0`")

    lines += [
        "━━━━━━━━━━━━━━━━━━",
        f"💰 *Total Tagihan (Priority A):* `{format_amount(total)}`",
    ]
    return "\n".join(lines)


async def cmd_credit(update, context):
    from services.sheets_service import get_debt_and_credit, parse_month_arg
    arg = " ".join(context.args).strip() if context.args else None
    target_month = parse_month_arg(arg)

    msg = await update.message.reply_text("⏳ Mengambil data hutang & cicilan...")
    result = get_debt_and_credit(target_month)
    msg_text = format_credit_message(result)
    reply_markup = kb_credit_nav(target_month)
    await msg.edit_text(msg_text, parse_mode="Markdown", reply_markup=reply_markup)


async def cmd_link(update, context):
    from services.sheets_service import SHEET_ID
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    msg = (
        "📊 *Google Spreadsheet Keuangan*\n\n"
        "Klik tombol di bawah atau link berikut untuk membuka spreadsheet Anda:\n"
        f"🔗 [Buka Google Sheets]({sheet_url})"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Buka Google Sheets", url=sheet_url),
            InlineKeyboardButton("❌ Tutup", callback_data="close_message"),
        ]
    ])
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)


# ── Message Handlers ──────────────────────────────────────────────────────────

async def handle_photo(update, context):
    from services.gemini_service import extract_from_image
    user_id = update.effective_user.id
    msg = await update.message.reply_text("📸 Membaca struk... ⏳")
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    data = extract_from_image(image_bytes)
    if "error" in data:
        await msg.edit_text(
            f"❌ Gagal membaca struk:\n`{data['error']}`\n\nCoba kirim teks saja.",
            parse_mode="Markdown",
        )
        return
    sessions[user_id] = {"data": data, "editing_field": None}
    await msg.edit_text(format_confirmation(data), parse_mode="Markdown", reply_markup=kb_confirm())


async def handle_text(update, context):
    from services.gemini_service import extract_from_text
    user_id = update.effective_user.id
    text = update.message.text.strip()
    session = sessions.get(user_id)
    if session and session.get("editing_field"):
        await _apply_edit_input(update, session, text)
        return
    msg = await update.message.reply_text("🤖 Menganalisis... ⏳")
    data = extract_from_text(text)
    if "error" in data:
        await msg.edit_text(
            f"❌ Gagal memproses:\n`{data['error']}`\n\nCoba tulis lebih detail.",
            parse_mode="Markdown",
        )
        return
    sessions[user_id] = {"data": data, "editing_field": None}
    await msg.edit_text(format_confirmation(data), parse_mode="Markdown", reply_markup=kb_confirm())


async def _apply_edit_input(update, session, text):
    field = session["editing_field"]
    data = session["data"]
    if field == "amount":
        clean = "".join(c for c in text if c.isdigit())
        data["amount"] = int(clean) if clean else data["amount"]
    elif field in ("date", "merchant", "notes", "category"):
        data[field] = text
    session["editing_field"] = None
    await update.message.reply_text(
        "✅ Diupdate!\n\n" + format_confirmation(data),
        parse_mode="Markdown",
        reply_markup=kb_confirm(),
    )


# ── Callback Query Handler ────────────────────────────────────────────────────

async def handle_callback(update, context):
    from services.sheets_service import append_expense, append_income, append_asset
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    session = sessions.get(user_id)
    cb = query.data

    if cb == "confirm_save":
        if not session:
            await query.edit_message_text("⚠️ Session habis. Kirim ulang transaksi.")
            return
        data = session["data"]
        tx_type = (data.get("type") or "expense").lower()
        date = data.get("date") or datetime.now().strftime("%d/%m/%Y")
        merchant = data.get("merchant") or "Unknown"
        amount = int(data.get("amount") or 0)
        category = data.get("category") or "Others"
        notes = data.get("notes") or ""
        await query.edit_message_text("💾 Menyimpan ke Google Sheets... ⏳")
        if tx_type == "income":
            result = append_income(date, merchant, amount, notes)
        elif tx_type == "asset":
            result = append_asset(merchant, notes, amount)
        else:
            cat = category if tx_type == "expense" else f"{category} ({tx_type.capitalize()})"
            result = append_expense(date, merchant, cat, amount, notes)
        if result.get("success"):
            sessions.pop(user_id, None)
            await query.edit_message_text(
                f"🎉 *Tersimpan!*\n\n✅ Transaksi #{result.get('no','?')} berhasil dicatat.\n"
                f"💰 {format_amount(amount)} → sheet bulan ini\n\nKirim foto atau teks berikutnya 📲",
                parse_mode="Markdown",
            )
        else:
            err = result.get("error", "Terjadi kesalahan.")
            await query.edit_message_text(f"❌ Gagal menyimpan:\n\n{err}")

    elif cb == "confirm_cancel":
        sessions.pop(user_id, None)
        await query.edit_message_text("❌ Transaksi dibatalkan.")

    elif cb == "confirm_edit":
        await query.edit_message_text("✏️ Pilih field yang ingin diedit:", reply_markup=kb_edit())

    elif cb == "edit_back":
        if not session:
            await query.edit_message_text("⚠️ Session habis.")
            return
        session["editing_field"] = None
        await query.edit_message_text(format_confirmation(session["data"]), parse_mode="Markdown", reply_markup=kb_confirm())

    elif cb == "edit_category":
        await query.edit_message_text("🗂️ Pilih kategori:", reply_markup=kb_category())

    elif cb.startswith("cat_"):
        if session:
            session["data"]["category"] = cb[4:]
            session["editing_field"] = None
            await query.edit_message_text(format_confirmation(session["data"]), parse_mode="Markdown", reply_markup=kb_confirm())

    elif cb == "edit_type":
        await query.edit_message_text("🔄 Pilih tipe transaksi:", reply_markup=kb_type())

    elif cb.startswith("type_"):
        if session:
            session["data"]["type"] = cb[5:]
            session["editing_field"] = None
            await query.edit_message_text(format_confirmation(session["data"]), parse_mode="Markdown", reply_markup=kb_confirm())

    elif cb in ("edit_date", "edit_merchant", "edit_amount", "edit_notes"):
        if not session:
            await query.edit_message_text("⚠️ Session habis. Kirim ulang transaksi.")
            return
        field = cb[5:]
        session["editing_field"] = field
        prompts = {
            "date":     "📅 Masukkan tanggal baru (format: DD/MM/YYYY):",
            "merchant": "🏪 Masukkan nama merchant/toko/sumber baru:",
            "amount":   "💰 Masukkan nominal baru (angka saja, contoh: 45000):",
            "notes":    "📝 Masukkan catatan baru:",
        }
        await query.edit_message_text(prompts.get(field, "Masukkan nilai baru:"))

    elif cb.startswith("credit_nav_"):
        try:
            target_month = int(cb.split("_")[2])
        except (IndexError, ValueError):
            target_month = datetime.now().month
        from services.sheets_service import get_debt_and_credit
        result = get_debt_and_credit(target_month)
        msg_text = format_credit_message(result)
        reply_markup = kb_credit_nav(target_month)
        await query.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=reply_markup)

    elif cb == "close_message":
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text("❌ Pesan ditutup.")


# ── Endpoint Handlers ─────────────────────────────────────────────────────────

def index():
    return Response("Budget Bot is running!", status=200)


def debug():
    """Health check — shows env var status without exposing secrets."""
    checks = {
        "TELEGRAM_BOT_TOKEN": "SET" if os.environ.get("TELEGRAM_BOT_TOKEN") else "MISSING",
        "OPENROUTER_API_KEY": "SET" if os.environ.get("OPENROUTER_API_KEY") else "MISSING",
        "GEMINI_API_KEY": "SET" if os.environ.get("GEMINI_API_KEY") else "MISSING",
        "GOOGLE_SHEET_ID": "SET" if os.environ.get("GOOGLE_SHEET_ID") else "MISSING",
        "GOOGLE_SERVICE_ACCOUNT_JSON": "SET" if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") else "MISSING",
        "request.path": request.path,
        "x-matched-path": request.headers.get("x-matched-path", ""),
    }
    try:
        import services.gemini_service
        checks["gemini_service import"] = "OK"
    except Exception as e:
        checks["gemini_service import"] = f"FAIL: {e}"
    try:
        import services.sheets_service
        checks["sheets_service import"] = "OK"
        sheet_info = services.sheets_service.inspect_sheet_structure()
        checks["sheet_rows_J_to_P"] = json.dumps(sheet_info.get("cells", []))
        checks["alloc_B6_E11"] = json.dumps(sheet_info.get("alloc_B6_E11", []))
        credit_sep = services.sheets_service.get_debt_and_credit()
        checks["test_credit_sep"] = f"Total: {credit_sep.get('total')}, items: {len(credit_sep.get('items', []))}"
    except Exception as e:
        checks["sheets_service import"] = f"FAIL: {e}"

    try:
        get_bot_app()
        checks["telegram_bot init"] = "OK"
    except Exception as e:
        checks["telegram_bot init"] = f"FAIL: {e}"

    body = "\n".join(f"{k}: {v}" for k, v in checks.items())
    return Response(body, status=200, mimetype="text/plain")


def webhook():
    raw = request.get_data()
    if not raw:
        return Response("Empty body", status=400)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return Response("Invalid JSON", status=400)

    try:
        asyncio.run(_process_update(payload))
    except Exception as e:
        logger.error(f"Webhook error: {e}\n{traceback.format_exc()}")
        return Response(f"Error: {e}", status=500)

    return Response("OK", status=200)


def set_webhook():
    import urllib.request as ur
    host = request.host_url.rstrip("/")
    if host.startswith("http://"):
        host = "https://" + host[7:]
    webhook_url = f"{host}/webhook"
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
    try:
        with ur.urlopen(api_url, timeout=10) as resp:
            result = json.loads(resp.read())
        return Response(
            f"Webhook set to: {webhook_url}\nTelegram response: {json.dumps(result, indent=2)}",
            status=200,
            mimetype="text/plain",
        )
    except Exception as e:
        return Response(f"Failed to set webhook: {e}", status=500, mimetype="text/plain")


def get_webhook_info():
    """Diagnostic endpoint to inspect Telegram's view of this webhook."""
    import urllib.request as ur
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
    try:
        with ur.urlopen(api_url, timeout=10) as resp:
            result = json.loads(resp.read())
        return Response(json.dumps(result, indent=2), status=200, mimetype="application/json")
    except Exception as e:
        return Response(f"Failed to get webhook info: {e}", status=500, mimetype="text/plain")


# ── Request Dispatcher (Handles both direct paths and Vercel rewrites) ────────

def _dispatch(path=""):

    clues = [
        path,
        request.path,
        request.environ.get("PATH_INFO", ""),
        request.headers.get("x-matched-path", ""),
        request.headers.get("x-forwarded-uri", ""),
        request.args.get("action", ""),
    ]
    raw = " ".join(str(c) for c in clues).lower()

    if request.method == "POST":
        return webhook()
    if "debug" in raw:
        return debug()
    if "get_webhook_info" in raw:
        return get_webhook_info()
    if "set_webhook" in raw:
        return set_webhook()
    return index()


@app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
@app.route("/<path:path>", methods=["GET", "POST"])
def catch_all(path=""):
    return _dispatch(path)


@app.errorhandler(404)
def handle_404(e):
    return _dispatch(request.path)


# Explicit WSGI callable for Granian / Vercel
application = app




