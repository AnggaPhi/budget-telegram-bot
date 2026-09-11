"""
Google Sheets Service
Reads and writes to the annual budget spreadsheet.

Sheet layout per monthly tab:
  K6:O33   → Expense transactions  (cols: No | Date | Description | Category | Amount)
  C7:E11   → Debt/Credit Priority A allocations
  C14:E18  → Monthly budget allocations (Priority B)
  C21:E25  → Others allocations (Priority C)
  G14:I24  → Income tracker
  G28:I33  → Assets tracker (Stock, Gold, Crypto)

Auth strategy (in priority order):
  1. Application Default Credentials — used automatically on Google Cloud Run
  2. GOOGLE_SERVICE_ACCOUNT_JSON env var — for local development / other hosting
"""

import os
import json
import re
from datetime import datetime

import gspread

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1bihj0mIqYCtzY1Vcr5j3yyZc6t2i4TSkCSKplvuV4i8")


# Month tab name mapping (handles both English and Indonesian)
MONTH_TABS = {
    1:  ["January", "Jan"],
    2:  ["February", "Feb"],
    3:  ["March", "Mar"],
    4:  ["April", "Apr"],
    5:  ["May", "Mei"],
    6:  ["June", "Jun"],
    7:  ["Juli", "Jul"],
    8:  ["August", "Agt"],
    9:  ["September", "Sep"],
    10: ["October", "Okt"],
    11: ["November", "Nov"],
    12: ["December", "Des"],
}

# Column mapping for expense table (K=11, L=12, M=13, N=14, O=15, P=16)
# Actual sheet columns: K=No, L=Date, M=Title, N=Description, O=Amount, P=Category
# Range: rows 5 to 34 (maximum 30 transactions)
EXPENSE_START_ROW = 5   # Row 5 (K5:P5)
EXPENSE_END_ROW = 34    # Row 34 (K34:P34)
MAX_EXPENSE_TRANSACTIONS = 30
EXPENSE_COL_NO = 11     # K  - Row number (K5:K34)
EXPENSE_COL_DATE = 12   # L  - Date (L5:L34)
EXPENSE_COL_TITLE = 13  # M  - Title / Merchant (M5:M34)
EXPENSE_COL_DESC = 14   # N  - Description / Notes (N5:N34)
EXPENSE_COL_AMT = 15    # O  - Amount (O5:O34)
EXPENSE_COL_CAT = 16    # P  - Category (P5:P34)



def _get_client() -> gspread.Client:
    """Authenticate and return a gspread client."""
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if service_account_json:
        from google.oauth2.service_account import Credentials
        try:
            info = json.loads(service_account_json)
        except json.JSONDecodeError:
            # Handle possible escaped JSON string
            info = json.loads(service_account_json.replace("\n", "\\n"))
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)

    # Fallback for GCP environments (Cloud Run, Compute Engine)
    try:
        from google.auth import default
        creds, _ = default(scopes=SCOPES)
        return gspread.authorize(creds)
    except Exception:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON environment variable is not set! "
            "Please paste your Google Service Account JSON in Vercel Settings -> Environment Variables."
        )



def _get_worksheet(month: int = None) -> gspread.Worksheet:
    """Get the worksheet for a given month (default: current month)."""
    if month is None:
        month = datetime.now().month

    client = _get_client()
    spreadsheet = client.open_by_key(SHEET_ID)

    # Try each possible tab name for this month
    for tab_name in MONTH_TABS.get(month, []):
        try:
            return spreadsheet.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            continue

    raise ValueError(f"No worksheet found for month {month}. Tried: {MONTH_TABS.get(month)}")


def append_expense(date: str, merchant: str, category: str, amount: int, notes: str = "", month: int = None) -> dict:
    """
    Append a new expense row to the transaction table (K5:P34).
    Columns:
      K: No
      L: Date
      M: Title (Merchant)
      N: Description (Notes)
      O: Amount
      P: Category
    Limit: maximum 30 transactions.
    Returns {"success": True, "row": N, "no": N} or {"success": False, "error": "..."}
    """
    try:
        ws = _get_worksheet(month)

        # Read existing expense rows (K5:P34)
        existing = ws.get(f"K{EXPENSE_START_ROW}:P{EXPENSE_END_ROW}")

        # Count used rows and find the next empty slot
        used_count = 0
        next_row = EXPENSE_START_ROW
        last_no = 0
        for i, row in enumerate(existing):
            # Check if col K (No.) has a value — meaning it's used
            val = row[0] if row else ""
            if str(val).strip():
                try:
                    parsed_no = int(str(val).strip())
                    last_no = max(last_no, parsed_no)
                except ValueError:
                    last_no += 1
                used_count += 1
                next_row = EXPENSE_START_ROW + i + 1
            else:
                # First empty row found
                next_row = EXPENSE_START_ROW + i
                break

        # Check limit (maximum 30 transactions, rows 5 to 34)
        if used_count >= MAX_EXPENSE_TRANSACTIONS or next_row > EXPENSE_END_ROW:
            return {
                "success": False,
                "error": f"⚠️ Batas maksimal {MAX_EXPENSE_TRANSACTIONS} transaksi tercapai (K{EXPENSE_START_ROW}:P{EXPENSE_END_ROW} penuh). Silakan hapus atau arsipkan transaksi lama di spreadsheet terlebih dahulu."
            }

        new_no = last_no + 1
        title = merchant
        description = notes or ""

        # Write the row: K=No, L=Date, M=Title, N=Description, O=Amount, P=Category
        ws.update(
            f"K{next_row}:P{next_row}",
            [[new_no, date, title, description, amount, category]]
        )

        return {"success": True, "row": next_row, "no": new_no}

    except Exception as e:
        return {"success": False, "error": str(e)}


def append_income(date: str, source: str, amount: int, notes: str = "", month: int = None) -> dict:
    """
    Append a new income entry to the income tracker (G14:I24).
    Columns: G=Source, H=Notes, I=Amount
    """
    try:
        ws = _get_worksheet(month)

        INCOME_START_ROW = 14
        INCOME_END_ROW = 24

        existing = ws.get(f"G{INCOME_START_ROW}:I{INCOME_END_ROW}")
        next_row = INCOME_START_ROW
        for i, row in enumerate(existing):
            val = row[0] if row else ""
            if str(val).strip():
                next_row = INCOME_START_ROW + i + 1
            else:
                next_row = INCOME_START_ROW + i
                break

        if next_row > INCOME_END_ROW:
            return {"success": False, "error": "Income table is full (G14:I24)."}

        ws.update(f"G{next_row}:I{next_row}", [[source, notes or date, amount]])
        return {"success": True, "row": next_row}

    except Exception as e:
        return {"success": False, "error": str(e)}


def append_asset(asset_type: str, name: str, amount: int, notes: str = "", month: int = None) -> dict:
    """
    Append an asset entry to the assets tracker (G28:I33).
    Columns: G=Type, H=Name/Notes, I=Value
    """
    try:
        ws = _get_worksheet(month)

        ASSET_START_ROW = 28
        ASSET_END_ROW = 33

        existing = ws.get(f"G{ASSET_START_ROW}:I{ASSET_END_ROW}")
        next_row = ASSET_START_ROW
        for i, row in enumerate(existing):
            val = row[0] if row else ""
            if str(val).strip():
                next_row = ASSET_START_ROW + i + 1
            else:
                next_row = ASSET_START_ROW + i
                break

        if next_row > ASSET_END_ROW:
            return {"success": False, "error": "Asset table is full (G28:I33)."}

        ws.update(f"G{next_row}:I{next_row}", [[asset_type, name or notes, amount]])
        return {"success": True, "row": next_row}

    except Exception as e:
        return {"success": False, "error": str(e)}


def update_debt_credit(row: int, label: str, amount: int, month: int = None) -> dict:
    """
    Update a debt/credit entry in Priority A allocations (C7:E11).
    row: 1-5 (relative row within C7:E11)
    Columns: C=Label, D=separator, E=Amount
    """
    try:
        ws = _get_worksheet(month)

        ALLOC_START_ROW = 7
        actual_row = ALLOC_START_ROW + (row - 1)

        if actual_row > 11:
            return {"success": False, "error": "Row out of range for debt/credit table (C7:E11)."}

        ws.update(f"C{actual_row}:E{actual_row}", [[label, ":", amount]])
        return {"success": True, "row": actual_row}

    except Exception as e:
        return {"success": False, "error": str(e)}


def _parse_cell_number(val) -> int:
    """Parse integer number from a sheet cell (handles 'Rp', commas, periods, spaces, negatives)."""
    if val is None:
        return 0
    s = str(val).replace("Rp", "").replace("IDR", "").strip()
    s = re.sub(r'[,.]00$', '', s)
    digits = re.sub(r'[^\d]', '', s)
    if not digits:
        return 0
    is_negative = '-' in str(val) or ('(' in str(val) and ')' in str(val))
    amt = int(digits)
    return -amt if is_negative else amt


def get_money_left(month: int = None) -> dict:
    """
    Calculate how much money is left based on:
    Income (Cell I25) minus Total Expenses (Cell I10).
    """
    try:
        ws = _get_worksheet(month)
        income_raw = ws.acell("I25").value
        expenses_raw = ws.acell("I10").value

        income = _parse_cell_number(income_raw)
        expenses = _parse_cell_number(expenses_raw)
        money_left = income - expenses

        return {
            "success": True,
            "income": income,
            "income_raw": income_raw,
            "expenses": expenses,
            "expenses_raw": expenses_raw,
            "money_left": money_left,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_monthly_summary(month: int = None) -> dict:
    """
    Read the summary totals for a given month.
    Returns total expenses, debt totals, income totals, and money left (I25 - I10).
    """
    try:
        ws = _get_worksheet(month)

        # Financial summary: Income (I25) minus Total Expenses (I10)
        income_raw = ws.acell("I25").value
        expenses_raw = ws.acell("I10").value

        income = _parse_cell_number(income_raw)
        expenses = _parse_cell_number(expenses_raw)
        money_left = income - expenses

        # Read all expense rows (K5:P34)
        expense_rows = ws.get(f"K{EXPENSE_START_ROW}:P{EXPENSE_END_ROW}")
        transactions = []
        for row in expense_rows:
            if row and str(row[0]).strip():
                transactions.append({
                    "no": row[0] if len(row) > 0 else "",
                    "date": row[1] if len(row) > 1 else "",
                    "title": row[2] if len(row) > 2 else "",
                    "description": row[3] if len(row) > 3 else "",
                    "amount": row[4] if len(row) > 4 else "",
                    "category": row[5] if len(row) > 5 and str(row[5]).strip() else "Others",
                })

        return {
            "success": True,
            "income": income,
            "income_raw": income_raw,
            "expenses": expenses,
            "expenses_raw": expenses_raw,
            "money_left": money_left,
            "transactions": transactions,
            "count": len(transactions),
            "max_transactions": MAX_EXPENSE_TRANSACTIONS,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def inspect_sheet_structure(month: int = None) -> dict:
    """Read headers and sample rows to check exact column layout and summary cells."""
    try:
        ws = _get_worksheet(month)
        # Read rows 4 to 8, columns J to Q
        cells = ws.get("J4:Q8")
        income_raw = ws.acell("I25").value
        expenses_raw = ws.acell("I10").value
        return {
            "success": True,
            "cells": cells,
            "I10_expenses": expenses_raw,
            "I25_income": income_raw,
            "money_left": _parse_cell_number(income_raw) - _parse_cell_number(expenses_raw),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


MONTH_NAMES_ID_EN = {
    1: ["januari", "january", "jan"],
    2: ["februari", "february", "feb"],
    3: ["maret", "march", "mar"],
    4: ["april", "apr"],
    5: ["mei", "may"],
    6: ["juni", "june", "jun"],
    7: ["juli", "july", "jul"],
    8: ["agustus", "august", "agt", "aug"],
    9: ["september", "sep"],
    10: ["oktober", "october", "okt", "oct"],
    11: ["november", "nov"],
    12: ["desember", "december", "des", "dec"],
}


def resolve_month_from_text(text: str) -> int:
    """
    Resolve which month (1-12) the user is referring to from natural language.
    Defaults to current month if no specific month keyword is found.
    """
    t = text.lower()
    curr_month = datetime.now().month

    # Check relative terms (Indonesian & English, handles "di bulan" or "dibulan")
    if re.search(r'\b(di\s*bulan\s+lalu|bulan\s+lalu|kemarin\s+lalu|last\s+month)\b', t):
        return 12 if curr_month == 1 else curr_month - 1
    if re.search(r'\b(di\s*bulan\s+depan|bulan\s+depan|next\s+month)\b', t):
        return 1 if curr_month == 12 else curr_month + 1
    if re.search(r'\b(di\s*bulan\s+ini|bulan\s+ini|sekarang|this\s+month)\b', t):
        return curr_month

    # Check explicit month names
    for m_num, aliases in MONTH_NAMES_ID_EN.items():
        for alias in aliases:
            if re.search(rf'\b{alias}\b', t):
                return m_num

    return curr_month


def get_debt_and_allocations(month: int = None) -> dict:
    """
    Read Priority A Allocations / Debt rows from C7:E11.
    Columns: C=Label, D=separator, E=Amount.
    """
    try:
        ws = _get_worksheet(month)
        alloc_rows = ws.get("C7:E11")
        items = []
        total = 0
        for row in alloc_rows:
            label = str(row[0]).strip() if len(row) > 0 else ""
            if label:
                val = row[2] if len(row) > 2 else (row[1] if len(row) > 1 and str(row[1]).strip() != ":" else "")
                amt = _parse_cell_number(val)
                items.append({"label": label, "amount": amt, "raw": str(val)})
                total += amt

        return {
            "success": True,
            "items": items,
            "total": total,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "items": [], "total": 0}


def build_compact_month_context(month: int = None) -> str:
    """
    Build an ultra-compact text snapshot (~60-120 tokens) of a month's spreadsheet data
    for conversational AI queries.
    """
    if month is None:
        month = datetime.now().month

    month_name_map = {
        1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
        7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember"
    }
    month_name = month_name_map.get(month, f"Bulan {month}")
    year = datetime.now().year

    summary = get_monthly_summary(month)
    if not summary.get("success"):
        return f"[Data {month_name} {year}: Belum ada data atau sheet belum tersedia]"

    income = summary.get("income", 0)
    expenses = summary.get("expenses", 0)
    money_left = summary.get("money_left", 0)
    txs = summary.get("transactions", [])

    # Get debt / Priority A allocations
    debt_info = get_debt_and_allocations(month)
    debts = debt_info.get("items", [])
    debt_total = debt_info.get("total", 0)

    lines = [f"[Data Keuangan Spreadsheet — {month_name} {year}]"]
    lines.append(f"• Total Income (I25): Rp {income:,}".replace(",", "."))
    lines.append(f"• Total Expenses (I10): Rp {expenses:,}".replace(",", "."))
    lines.append(f"• Sisa Uang: Rp {money_left:,}".replace(",", "."))

    if debts and any(d['amount'] > 0 for d in debts):
        debt_items = [f"{d['label']}: Rp {d['amount']:,}".replace(",", ".") for d in debts if d['amount'] > 0]
        lines.append(f"• Hutang/Alokasi Priority A (C7:E11): {', '.join(debt_items)} (Total: Rp {debt_total:,})".replace(",", "."))
    else:
        lines.append("• Hutang/Alokasi Priority A: Tidak ada hutang tercatat (Rp 0)")

    if txs:
        cat_totals = {}
        for tx in txs:
            amt = _parse_cell_number(tx.get("amount", 0))
            c = str(tx.get("category") or "").strip() or "Others"
            cat_totals[c] = cat_totals.get(c, 0) + amt

        cat_str = ", ".join(f"{k}: Rp {v:,}".replace(",", ".") for k, v in sorted(cat_totals.items(), key=lambda x: -x[1]))
        lines.append(f"• Kategori Pengeluaran ({len(txs)} transaksi): {cat_str}")

        tx_items = []
        for t in txs:
            title = t.get("title") or t.get("description") or "-"
            notes = f" ({t['description']})" if t.get("description") and t.get("description") != title else ""
            amt = _parse_cell_number(t.get("amount", 0))
            cat = t.get("category") or "Others"
            tx_items.append(f"- {t.get('date', '-')}: {title}{notes} | Rp {amt:,} | {cat}".replace(",", "."))
        lines.append("• Daftar Transaksi:\n  " + "\n  ".join(tx_items))
    else:
        lines.append("• Daftar Transaksi: Belum ada transaksi pengeluaran tercatat.")

    return "\n".join(lines)

