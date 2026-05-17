"""
Celery tasks package.

Beat schedule (NFR4):
  - tasks.daily_sales_batch.run_daily_sales        @ 02:00 UTC every day

Tasks are split by responsibility:
  - notifications.py : low-priority queue (email, push)
  - invoicing.py     : medium-priority queue (PDF generation)
  - daily_sales_batch.py : long-running scheduled batch job
"""
from celery.schedules import crontab

from config.celery import app
from . import notifications
from . import invoicing

app.conf.beat_schedule = {
    "daily-sales-batch": {
        "task": "tasks.daily_sales_batch.run_daily_sales",
        "schedule": crontab(hour=2, minute=0),
    },
    "warm-product-cache": {
        # [NFR6] keep the hottest catalog rows in Redis ahead of peak traffic.
        "task": "tasks.notifications.warm_product_cache",
        "schedule": crontab(minute="*/15"),
    },
}
