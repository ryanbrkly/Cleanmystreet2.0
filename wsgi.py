"""WSGI entry point: gunicorn wsgi:app"""
from cms import create_app

app = create_app()
