from rest_framework import serializers

from apps.products.serializers import ProductListSerializer

from .models import Cart, CartItem


class CartItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)
    product_id = serializers.IntegerField(write_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id", "product", "product_id", "quantity", "unit_price", "line_total",
        ]
        read_only_fields = ["unit_price"]

    def get_line_total(self, obj: CartItem) -> str:
        return str(obj.unit_price * obj.quantity)


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = [
            "id", "status", "version", "items",
            "item_count", "subtotal", "updated_at",
        ]

    def get_subtotal(self, obj: Cart) -> str:
        total = sum((it.unit_price * it.quantity for it in obj.items.all()), start=0)
        return str(total)

    def get_item_count(self, obj: Cart) -> int:
        return sum(it.quantity for it in obj.items.all())


class AddItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)


class UpdateItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=0)
