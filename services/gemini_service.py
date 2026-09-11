"""
AI Extraction Service (OpenRouter & Gemini)
Extracts structured transaction data from receipt images or free text.
Supports OpenRouter (free vision models) and Google Gemini.
"""

import os
import sys
import json
import re
import base64
import urllib.request
from datetime import datetime

CATEGORIES = [
    "Food & Drinks",
    "Shopping",
    "Bills & Utilities",
    "Entertainment",
    "Groceries",
    "Education",
    "Health",
    "Personal Care",
    "Transport",
    "Others",
]

TRANSACTION_TYPES = ["expense", "income", "debt", "credit", "asset"]

SYSTEM_PROMPT = f"""
You are a financial assistant helping to log transactions into a personal budget spreadsheet.

Today's date is: {datetime.now().strftime("%d %B %Y")}
Currency: Indonesian Rupiah (IDR/Rp)
Available expense categories: {", ".join(CATEGORIES)}
Transaction types: expense, income, debt (money I owe someone), credit (money someone owes me), asset (stock/gold/crypto update)

Extract the transaction details and return ONLY a valid JSON object with these fields:
{{
  "type": "expense" | "income" | "debt" | "credit" | "asset",
  "date": "DD/MM/YYYY",
  "merchant": "store or person name",
  "amount": 12345 (number only, no currency symbol, no dots/commas),
  "category": "one of the categories above (for expense only)",
  "notes": "brief description",
  "confidence": "high" | "medium" | "low"
}}

Rules:
- For income: merchant = source of income (e.g. "Salary", "Freelance")
- For debt: merchant = person I borrowed from, notes = what for
- For credit: merchant = person who owes me, notes = what for  
- For asset: merchant = asset type (Stock/Gold/Crypto), notes = asset name and action
- If a field cannot be determined, use null
- Return ONLY the JSON object, do not wrap in markdown or explain.
"""


def _call_openrouter(messages: list, is_vision: bool = False) -> str:
    """Send chat completions request to OpenRouter API with multi-provider fallbacks."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY environment variable is not set.")

    if is_vision:
        # Vision-capable free models across providers
        primary_model = "google/gemma-4-31b-it:free"
        models_list = [
            "google/gemma-4-31b-it:free",
            "inclusionai/ling-3.0-flash-vl:free",
            "google/gemma-4-26b-a4b-it:free",
            "thinkingmachines/inkling:free",
            "nex-agi/nex-n2.5-mini:free",
        ]
    else:
        # Multi-provider free models to avoid single-provider 429 rate limits
        primary_model = "google/gemma-4-31b-it:free"
        models_list = [
            "google/gemma-4-31b-it:free",
            "liquid/lfm-2.5-2.6b:free",
            "nvidia/nemotron-3.5-lightning:free",
            "nex-agi/nex-n2.5-mini:free",
            "thinkingmachines/inkling:free",
            "google/gemma-4-26b-a4b-it:free",
            "inclusionai/ling-3.0-flash-vl:free",
            "poolside/laguna-s-2.1:free",
        ]

    model = os.environ.get("OPENROUTER_MODEL", primary_model)

    payload = {
        "model": model,
        "models": models_list,
        "messages": messages,
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/AnggaPhi/budget-telegram-bot",
        "X-Title": "Budget Telegram Bot",
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            choices = res_data.get("choices", [])
            if not choices:
                raise RuntimeError(f"OpenRouter empty response: {res_data}")
            return choices[0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenRouter error {e.code}: {err_msg}")



def _call_gemini(prompt: str, image_bytes: bytes = None, mime_type: str = "image/jpeg") -> str:
    """Fallback to Gemini API if configured."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Neither OPENROUTER_API_KEY nor GEMINI_API_KEY is set.")

    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    if image_bytes:
        image_part = {"mime_type": mime_type, "data": image_bytes}
        res = model.generate_content([prompt, image_part])
    else:
        res = model.generate_content(prompt)
    return res.text


def extract_from_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Extract transaction data from a receipt image."""
    prompt = SYSTEM_PROMPT + "\n\nAnalyze this receipt image and extract the transaction details."

    try:
        if os.environ.get("OPENROUTER_API_KEY"):
            base64_img = base64.b64encode(image_bytes).decode("utf-8")
            data_uri = f"data:{mime_type};base64,{base64_img}"
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri}
                        }
                    ]
                }
            ]
            raw = _call_openrouter(messages, is_vision=True)
        else:
            raw = _call_gemini(prompt, image_bytes=image_bytes, mime_type=mime_type)

        return _parse_response(raw)
    except Exception as e:
        return {"error": str(e)}


def extract_from_text(text: str) -> dict:
    """Extract transaction data from a free-text message."""
    prompt = SYSTEM_PROMPT + f'\n\nExtract transaction details from this message:\n\n"{text}"'

    try:
        if os.environ.get("OPENROUTER_API_KEY"):
            messages = [{"role": "user", "content": prompt}]
            raw = _call_openrouter(messages, is_vision=False)
        else:
            raw = _call_gemini(prompt)

        return _parse_response(raw)
    except Exception as e:
        return {"error": str(e)}


def _parse_response(raw: str) -> dict:
    """Clean and parse the JSON response from the AI model."""
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    # Find JSON substring if model included extra commentary
    json_match = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
    if json_match:
        raw = json_match.group(1)

    try:
        data = json.loads(raw)
        if data.get("amount") is not None:
            try:
                data["amount"] = int(str(data["amount"]).replace(",", "").replace(".", "").strip())
            except (ValueError, TypeError):
                pass
        return data
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse AI response: {e}", "raw": raw}


FINANCIAL_ASSISTANT_SYSTEM_PROMPT = """Anda adalah asisten keuangan pribadi yang cerdas, ramah, dan ringkas.
Tugas Anda adalah menjawab pertanyaan pengguna HANYA berdasarkan konteks data spreadsheet yang diberikan.

Aturan Ketat:
1. Jawab secara padat, jelas, ramah, dalam Bahasa Indonesia (maksimal 2-4 kalimat atau poin ringkas).
2. Sebutkan angka uang dengan format Rupiah (contoh: Rp 50.000).
3. Jika pertanyaan menanyakan hal di luar data keuangan / spreadsheet yang diberikan (misal: pengetahuan umum, coding, resep, ngobrol di luar keuangan), tolak dengan sopan:
   "Maaf, saya hanya asisten keuangan pribadi Anda. Saya hanya bisa menjawab pertanyaan seputar data keuangan di spreadsheet Anda."
4. Jangan mengarang data yang tidak tercantum di konteks spreadsheet.
"""


def answer_financial_query(user_message: str, sheet_context: str) -> str:
    """
    Generate a concise, friendly conversational response based strictly on spreadsheet data.
    Gracefully falls back to direct spreadsheet data extraction if OpenRouter is rate-limited.
    """
    user_prompt = f"{sheet_context}\n\nPertanyaan: \"{user_message}\"\nJawaban:"
    messages = [
        {"role": "system", "content": FINANCIAL_ASSISTANT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]

    try:
        if os.environ.get("OPENROUTER_API_KEY"):
            return _call_openrouter(messages, is_vision=False).strip()
        else:
            full_prompt = f"{FINANCIAL_ASSISTANT_SYSTEM_PROMPT}\n\n{user_prompt}"
            return _call_gemini(full_prompt).strip()
    except Exception:
        # Graceful fallback: return direct spreadsheet data instead of breaking with 429 error
        return _format_local_fallback(user_message, sheet_context)


def _format_local_fallback(user_message: str, sheet_context: str) -> str:
    """
    Directly extracts and formats answers from sheet_context without AI tokens
    when OpenRouter is rate-limited or unavailable.
    """
    u = user_message.lower().strip()

    # 1. Sisa Uang / Saldo
    if any(w in u for w in ("sisa", "saldo", "balance")):
        match = re.search(r'• Sisa Uang[^\n]*:\s*([^\n]+)', sheet_context)
        sisa = match.group(1).strip() if match else "-"
        return f"💰 *Sisa Uang Anda:* `{sisa}`\n\n_(Data langsung dari spreadsheet)_"

    # 2. Hutang / Alokasi
    if any(w in u for w in ("hutang", "utang", "cicilan", "tagihan")):
        match = re.search(r'• Hutang/Alokasi[^\n]*:\s*([^\n]+)', sheet_context)
        hutang = match.group(1).strip() if match else "Tidak ada hutang tercatat (Rp 0)"
        return f"🔴 *Hutang / Alokasi Priority A:*\n{hutang}\n\n_(Data langsung dari spreadsheet)_"

    # 3. Transaksi kemarin / hari ini / riwayat
    if any(w in u for w in ("kemarin", "hari ini", "tadi", "daftar", "rincian", "beli apa")):
        tx_lines = [line.strip() for line in sheet_context.split("\n") if line.strip().startswith("- ")]
        if tx_lines:
            recent = "\n".join(tx_lines[-8:])
            return f"📋 *Catatan Transaksi:*\n{recent}\n\n_(Data langsung dari spreadsheet)_"
        else:
            return "📋 Belum ada transaksi pengeluaran yang tercatat di spreadsheet untuk bulan ini."

    # 4. Out-of-scope question detection in fallback
    if any(w in u for w in ("presiden", "siapa", "coding", "python", "resep", "cuaca", "berita")) and not any(w in u for w in ("uang", "biaya", "pengeluaran", "pemasukan", "hutang", "transaksi", "spreadsheet", "budget")):
        return "Maaf, saya hanya asisten keuangan pribadi Anda. Saya hanya bisa menjawab pertanyaan seputar data keuangan di spreadsheet Anda."

    # 5. General summary fallback
    clean_lines = []
    for line in sheet_context.split("\n"):
        if line.startswith("[Data Keuangan"):
            clean_lines.append(f"📊 *{line.strip('[]')}*")
        elif line.startswith("• "):
            clean_lines.append(line)
        elif line.startswith("- "):
            clean_lines.append(f"  {line}")
    return "\n".join(clean_lines) if clean_lines else sheet_context


