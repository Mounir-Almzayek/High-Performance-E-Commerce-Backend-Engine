from django.contrib import admin

from .models import Address, Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "phone",
        "wallet_balance",
        "loyalty_points",
        "version",
        "created_at",
    )
    search_fields = ("user__username", "user__email", "phone")


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "kind", "city", "country", "is_default")
    list_filter = ("kind", "country", "is_default")
