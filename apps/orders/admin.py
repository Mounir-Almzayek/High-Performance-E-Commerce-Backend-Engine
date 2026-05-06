from django.contrib import admin

from .models import DailySalesReport, Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product_sku", "product_name",
                       "unit_price", "quantity", "line_total")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "public_id", "customer", "status",
                    "total", "currency", "placed_at")
    list_filter = ("status", "currency")
    search_fields = ("public_id", "customer__user__username")
    readonly_fields = ("public_id", "version")
    date_hierarchy = "placed_at"
    inlines = [OrderItemInline]


@admin.register(DailySalesReport)
class DailySalesReportAdmin(admin.ModelAdmin):
    list_display = ("date", "total_orders", "total_revenue",
                    "total_items_sold", "created_at")
    list_filter = ("date",)
    date_hierarchy = "date"
    readonly_fields = ("created_at", "updated_at")
