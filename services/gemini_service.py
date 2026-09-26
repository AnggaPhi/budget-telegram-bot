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
  "date": "M/D/YYYY",
  "merchant": "store or person name",
  "amount": 12345 (raw integer only, no currency symbol, no dots/commas),
  "category": "one of the categories above (for expense only)",
  "notes": "brief description",
  "confidence": "high" | "medium" | "low"
}}

Rules:
- Format date strictly as Month/Day/Year (e.g., 9/19/2026, 9/25/2026).
- Understand Indonesian number shorthands:
  * "k", "rb", "ribu" = thousand (e.g. "12k" -> 12000, "12.5k" -> 12500, "25rb" -> 25000, "500k" -> 500000)
  * "jt", "juta", "m" = million (e.g. "1.5jt" -> 1500000, "2jt" -> 2000000)
- Always convert amount to a clean integer (e.g. 12k must become 12000, not 12)
- For income: merchant = source of income (e.g. "Salary", "Freelance")
- For debt: merchant = person I borrowed from, notes = what for
- For credit: merchant = person who owes me, notes = what for  
- For asset: merchant = asset type (Stock/Gold/Crypto), notes = asset name and action
- If a field cannot be determined, use null
- Return ONLY the JSON object, do not wrap in markdown or explain.
"""


def _call_openrouter(messages: list, is_vision: bool = False) -> str:
    """Send chat completions request to OpenRouter API with multi-provider fallback batches (max 3 per batch)."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY environment variable is not set.")

    if is_vision:
        batches = [
            ["google/gemini-2.5-flash:free", "google/gemini-2.0-flash-lite-preview-02-05:free", "google/gemini-exp-1206:free"],
            ["meta-llama/llama-3.2-90b-vision-instruct:free", "google/gemini-2.0-pro-exp-02-05:free"],
        ]
    else:
        batches = [
            ["google/gemini-2.5-flash:free", "google/gemini-2.0-flash-lite-preview-02-05:free", "google/gemma-2-9b-it:free"],
            ["meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen-2.5-72b-instruct:free"],
        ]

    custom_model = os.environ.get("OPENROUTER_MODEL")
    if custom_model:
        batches[0] = [custom_model] + [m for m in batches[0] if m != custom_model][:2]

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/AnggaPhi/budget-telegram-bot",
        "X-Title": "Budget Telegram Bot",
    }

    last_error = None
    for batch in batches:
        payload = {
            "model": batch[0],
            "models": batch[:3],  # OpenRouter requires maximum 3 items in models array
            "messages": messages,
            "temperature": 0.1,
        }

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                choices = res_data.get("choices", [])
                if choices:
                    return choices[0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            last_error = f"OpenRouter error {e.code}: {err_msg}"
            continue
        except Exception as e:
            last_error = str(e)
            continue

    raise RuntimeError(last_error or "OpenRouter failed on all fallback models.")



def _call_gemini(prompt: str, image_bytes: bytes = None, mime_type: str = "image/jpeg") -> str:
    """Fallback to Gemini API if configured."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Neither OPENROUTER_API_KEY nor GEMINI_API_KEY is set.")

    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

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
        raw = None
        if os.environ.get("OPENROUTER_API_KEY"):
            try:
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
            except Exception as e:
                # Fallback to direct Gemini API if OpenRouter hits a 429 rate limit or fails
                if os.environ.get("GEMINI_API_KEY"):
                    raw = _call_gemini(prompt, image_bytes=image_bytes, mime_type=mime_type)
                else:
                    raise e
        else:
            raw = _call_gemini(prompt, image_bytes=image_bytes, mime_type=mime_type)

        return _parse_response(raw)
    except Exception as e:
        return {"error": str(e)}


def extract_from_text(text: str) -> dict:
    """Extract transaction data from a free-text message."""
    prompt = SYSTEM_PROMPT + f'\n\nExtract transaction details from this message:\n\n"{text}"'

    try:
        raw = None
        if os.environ.get("OPENROUTER_API_KEY"):
            try:
                messages = [{"role": "user", "content": prompt}]
                raw = _call_openrouter(messages, is_vision=False)
            except Exception as e:
                if os.environ.get("GEMINI_API_KEY"):
                    raw = _call_gemini(prompt)
                else:
                    raise e
        else:
            raw = _call_gemini(prompt)

        return _parse_response(raw)
    except Exception as e:
        return {"error": str(e)}


def parse_shorthand_amount(val) -> int:
    """Parse number with support for k/rb (thousand) and jt/m (million)."""
    if val is None:
        return 0
    s = str(val).lower().replace("rp", "").replace("idr", "").strip()

    # Match 'k', 'rb', 'ribu'
    m_k = re.search(r'([\d]+(?:[.,]\d+)?)\s*(k|rb|ribu)\b', s)
    if m_k:
        num_str = m_k.group(1).replace(",", ".")
        try:
            return int(float(num_str) * 1000)
        except ValueError:
            pass

    # Match 'jt', 'juta', 'm', 'mio', 'million'
    m_m = re.search(r'([\d]+(?:[.,]\d+)?)\s*(jt|juta|m|mio|million)\b', s)
    if m_m:
        num_str = m_m.group(1).replace(",", ".")
        try:
            return int(float(num_str) * 1000000)
        except ValueError:
            pass

    # Standard clean up
    s = re.sub(r'[,.]00$', '', s)
    digits = re.sub(r'[^\d]', '', s)
    return int(digits) if digits else 0


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
            data["amount"] = parse_shorthand_amount(data["amount"])
        return data
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse AI response: {e}", "raw": raw}


