from rest_framework import serializers

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id", "product", "product_sku", "product_name",
            "unit_price", "quantity", "line_total",
        ]
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "public_id", "status",
            "subtotal", "tax", "shipping_fee", "total", "currency",
            "shipping_address", "billing_address",
            "items", "version", "placed_at", "updated_at",
        ]
        read_only_fields = [
            "public_id", "status", "subtotal", "tax", "shipping_fee",
            "total", "currency", "items", "version", "placed_at", "updated_at",
        ]


class PlaceOrderSerializer(serializers.Serializer):
    """Body for POST /api/v1/orders/place/."""

    shipping_address_id = serializers.IntegerField()
    billing_address_id = serializers.IntegerField()


class CancelOrderSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)
