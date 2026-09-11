"""
Vercel Serverless Function Entry Point
Modern Vercel automatically maps requests to api/index.py
"""
import os
import sys

# Ensure ROOT_DIR is on sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from api.webhook import app

# Explicit WSGI callable for Granian / Vercel
application = app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
