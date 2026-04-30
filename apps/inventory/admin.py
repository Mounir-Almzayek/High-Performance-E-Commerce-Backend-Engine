from django.contrib import admin

from .models import StockItem, StockMovement


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "on_hand", "reserved", "available", "version", "updated_at")
    search_fields = ("product__sku", "product__name")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("id", "stock_item", "kind", "quantity", "reference", "created_at")
    list_filter = ("kind",)
    search_fields = ("reference",)
    date_hierarchy = "created_at"
