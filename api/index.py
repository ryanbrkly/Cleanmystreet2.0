"""Vercel entry point. Vercel's Python runtime serves the WSGI `app` defined here."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms import create_app  # noqa: E402

app = create_app()
