from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import PaymentIntentViewSet, WebhookView

router = DefaultRouter()
router.register("intents", PaymentIntentViewSet, basename="intent")

urlpatterns = [
    path("webhook/", WebhookView.as_view(), name="payment-webhook"),
    path("", include(router.urls)),
]
