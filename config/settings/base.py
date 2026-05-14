"""
Base settings shared by dev and prod.

Conventions for reviewers:
 - Every setting tied to a non-functional requirement is annotated with
   an [NFR-x] tag matching docs/requirements/<n>-*.md.
 - Touching such a setting REQUIRES updating its companion doc.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["*"])

# -- Apps --------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework.authtoken",  # Token auth for external API testing (Postman).
    "silk",  # [AOP] Request- and query-level profiling.
]

LOCAL_APPS = [
    "apps.users",
    "apps.products",
    "apps.cart",
    "apps.orders",
    "apps.inventory",
    "apps.payments",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# -- Middleware --------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # [AOP] Custom middleware that times every request and tags it with the
    #       INSTANCE_ID, used by the NFR5 distribution and NFR10 reports.
    "core.aop.middleware.PerformanceMiddleware",
    "silk.middleware.SilkyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# -- Database ----------------------------------------------------------------
# [NFR8] PostgreSQL with default isolation level READ COMMITTED.
# [NFR7] CONN_MAX_AGE > 0 enables persistent connections (prevents per-request
#        connect/teardown overhead under load, but mind pg's max_connections).
# [NFR8] ATOMIC_REQUESTS=False because we control transaction boundaries
#        explicitly (see core/transactions/atomic.py).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": 60,
        "ATOMIC_REQUESTS": False,
    }
}

# -- Cache (NFR6) ------------------------------------------------------------
# Redis as a distributed cache shared by web1 and web2.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("CACHE_URL"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

# -- Sessions backed by Redis (required for stateless load balancing) -------
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# -- Celery (NFR3, NFR4) -----------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_TASK_ACKS_LATE = True              # ack only after successful execution
CELERY_TASK_REJECT_ON_WORKER_LOST = True  # re-queue if a worker crashes
CELERY_TASK_TIME_LIMIT = 300

# -- DRF ---------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# -- I18N / TZ ---------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -- Project-specific tunables tied to NFRs ----------------------------------
# [NFR2] Outer caps are owned by Gunicorn/Celery, but exposed here so the
#        diagnostics endpoint can report the live capacity budget.
GUNICORN_WORKERS = env.int("GUNICORN_WORKERS", default=4)
GUNICORN_THREADS = env.int("GUNICORN_THREADS", default=2)
GUNICORN_WORKER_CLASS = env("GUNICORN_WORKER_CLASS", default="sync")
GUNICORN_TIMEOUT = env.int("GUNICORN_TIMEOUT", default=30)
CELERY_CONCURRENCY = env.int("CELERY_CONCURRENCY", default=4)

# [NFR2] Inner-process concurrency caps used by core.resources.pool.
INTERNAL_POOL_MAX_CONCURRENCY = env.int("INTERNAL_POOL_MAX_CONCURRENCY", default=16)
RESOURCE_CHECKOUT_MAX_CONCURRENCY = env.int("RESOURCE_CHECKOUT_MAX_CONCURRENCY", default=8)
RESOURCE_PAYMENT_MAX_CONCURRENCY = env.int("RESOURCE_PAYMENT_MAX_CONCURRENCY", default=8)
RESOURCE_BATCH_MAX_CONCURRENCY = env.int("RESOURCE_BATCH_MAX_CONCURRENCY", default=4)
RESOURCE_ACQUIRE_TIMEOUT_SECONDS = float(
    env("RESOURCE_ACQUIRE_TIMEOUT_SECONDS", default="1.0")
)
RESOURCE_LIMITS = {
    "internal_pool": INTERNAL_POOL_MAX_CONCURRENCY,
    "checkout": RESOURCE_CHECKOUT_MAX_CONCURRENCY,
    "payment": RESOURCE_PAYMENT_MAX_CONCURRENCY,
    "batch": RESOURCE_BATCH_MAX_CONCURRENCY,
}

# Identifier injected by docker-compose so logs can reveal which instance
# served a given request (used by NFR5 / NFR10 reports).
INSTANCE_ID = env("INSTANCE_ID", default="local")
