"""
Entry point for Google Cloud Run.
Gunicorn runs this file: gunicorn main:app
"""
import os
import sys

# Ensure the project root is always on the Python path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.webhook import app  # noqa: E402 – must be after sys.path fix

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
