from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

INTERNAL_IPS = ["127.0.0.1"]

# Verbose logging during development to make concurrency hot-spots visible.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "core.aop": {"level": "DEBUG", "propagate": True},
        # Bump to DEBUG to see every SQL statement.
        "django.db.backends": {"level": "INFO"},
    },
}
