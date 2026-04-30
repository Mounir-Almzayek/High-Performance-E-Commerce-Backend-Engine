from django.contrib import admin

from .models import PaymentIntent, WebhookEvent


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = ("id", "public_id", "order", "amount", "currency", "status", "version", "updated_at")
    list_filter = ("status", "currency")
    search_fields = ("public_id", "external_id")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "signature", "received_at", "processed_at")
    readonly_fields = ("signature", "payload", "received_at", "processed_at")
