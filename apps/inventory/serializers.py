from rest_framework import serializers

from .models import StockItem, StockMovement


class StockItemSerializer(serializers.ModelSerializer):
    available = serializers.IntegerField(read_only=True)

    class Meta:
        model = StockItem
        fields = [
            "id", "product", "on_hand", "reserved", "available",
            "reorder_threshold", "version", "updated_at",
        ]
        read_only_fields = ["on_hand", "reserved", "version", "updated_at"]


class StockMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockMovement
        fields = ["id", "stock_item", "kind", "quantity", "reference", "created_at"]
        read_only_fields = fields


class AdjustStockSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    qty = serializers.IntegerField(min_value=1)
    reference = serializers.CharField(max_length=128)
