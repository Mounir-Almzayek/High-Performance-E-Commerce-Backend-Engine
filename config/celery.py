"""
Celery setup - [NFR3] async queues + [NFR4] batch processing.

- Uses Redis as broker and result backend (see settings/base.py).
- autodiscover_tasks picks up tasks under apps/* and tasks/*.
- The celery beat schedule for the daily batch is defined in tasks/__init__.py.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("ecommerce_engine")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["apps"])
