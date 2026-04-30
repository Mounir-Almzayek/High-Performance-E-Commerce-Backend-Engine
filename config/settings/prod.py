from .base import *  # noqa: F401,F403

DEBUG = False

# سيتم ضبط ALLOWED_HOSTS عبر env في الإنتاج.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
