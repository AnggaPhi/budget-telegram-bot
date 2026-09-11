"""
Gemini AI Service
Extracts structured transaction data from receipt images or free text.
"""

import os
import json
import re
import google.generativeai as genai
from datetime import datetime

# Lazy-initialized — configured on first use, not at import time
_model = None


def _get_model():
    """Return a configured Gemini model, initializing on first call."""
    global _model
    if _model is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable is not set.")
        genai.configure(api_key=api_key)
        _model = genai.GenerativeModel("gemini-1.5-flash")
    return _model


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
- Return ONLY the JSON, no extra text
"""


def extract_from_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """Extract transaction data from a receipt image."""
    image_part = {"mime_type": mime_type, "data": image_bytes}
    prompt = SYSTEM_PROMPT + "\n\nAnalyze this receipt image and extract the transaction details."

    try:
        response = _get_model().generate_content([prompt, image_part])
        return _parse_response(response.text)
    except Exception as e:
        return {"error": str(e)}


def extract_from_text(text: str) -> dict:
    """Extract transaction data from a free-text message."""
    prompt = SYSTEM_PROMPT + f"\n\nExtract transaction details from this message:\n\n\"{text}\""

    try:
        response = _get_model().generate_content(prompt)
        return _parse_response(response.text)
    except Exception as e:
        return {"error": str(e)}



def _parse_response(raw: str) -> dict:
    """Clean and parse the JSON response from Gemini."""
    # Strip markdown code blocks if present
    raw = raw.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    try:
        data = json.loads(raw)
        # Normalize amount to integer
        if data.get("amount") is not None:
            try:
                data["amount"] = int(str(data["amount"]).replace(",", "").replace(".", "").strip())
            except (ValueError, TypeError):
                pass
        return data
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse AI response: {e}", "raw": raw}
