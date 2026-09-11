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

# Column mapping for expense table (K=11, L=12, M=13, N=14, O=15)
# Actual sheet columns: K=No, L=Date, M=Title, N=Amount, O=Category
# Range: rows 5 to 34 (maximum 30 transactions)
EXPENSE_START_ROW = 5   # Row 5 (K5:O5)
EXPENSE_END_ROW = 34    # Row 34 (K34:O34)
MAX_EXPENSE_TRANSACTIONS = 30
EXPENSE_COL_NO = 11     # K  - Row number (K5:K34)
EXPENSE_COL_DATE = 12   # L  - Date (L5:L34)
EXPENSE_COL_DESC = 13   # M  - Description / Title (M5:M34)
EXPENSE_COL_AMT = 14    # N  - Amount (N5:N34)
EXPENSE_COL_CAT = 15    # O  - Category (O5:O34)



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
    Append a new expense row to the transaction table (K5:O34).
    Limit: maximum 30 transactions.
    Returns {"success": True, "row": N, "no": N} or {"success": False, "error": "..."}
    """
    try:
        ws = _get_worksheet(month)

        # Read existing expense rows (K5:O34)
        existing = ws.get(f"K{EXPENSE_START_ROW}:O{EXPENSE_END_ROW}")

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
                "error": f"⚠️ Batas maksimal {MAX_EXPENSE_TRANSACTIONS} transaksi tercapai (K{EXPENSE_START_ROW}:K{EXPENSE_END_ROW} penuh). Silakan hapus atau arsipkan transaksi lama di spreadsheet terlebih dahulu."
            }

        new_no = last_no + 1
        description = f"{merchant}" + (f" - {notes}" if notes else "")

        # Write the row
        ws.update(
            f"K{next_row}:O{next_row}",
            [[new_no, date, description, amount, category]]
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


def get_monthly_summary(month: int = None) -> dict:
    """
    Read the summary totals for a given month.
    Returns total expenses, debt totals, income totals.
    """
    try:
        ws = _get_worksheet(month)

        # Read key summary cells
        expense_total = ws.acell("E11").value  # Total Expenses area
        debt_total = ws.acell("E8").value

        # Read all expense rows
        expense_rows = ws.get(f"K{EXPENSE_START_ROW}:O{EXPENSE_END_ROW}")
        transactions = []
        for row in expense_rows:
            if row and str(row[0]).strip():
                transactions.append({
                    "no": row[0] if len(row) > 0 else "",
                    "date": row[1] if len(row) > 1 else "",
                    "description": row[2] if len(row) > 2 else "",
                    "amount": row[3] if len(row) > 3 else "",
                    "category": row[4] if len(row) > 4 and str(row[4]).strip() else "Others",
                })


        return {
            "success": True,
            "transactions": transactions,
            "count": len(transactions),
            "max_transactions": MAX_EXPENSE_TRANSACTIONS,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def inspect_sheet_structure(month: int = None) -> dict:
    """Read headers and sample rows to check exact column layout."""
    try:
        ws = _get_worksheet(month)
        # Read rows 4 to 8, columns J to P
        cells = ws.get("J4:P8")
        return {"success": True, "cells": cells}
    except Exception as e:
        return {"success": False, "error": str(e)}

