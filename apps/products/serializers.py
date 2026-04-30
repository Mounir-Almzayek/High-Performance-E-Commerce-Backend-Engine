from rest_framework import serializers

from .models import Category, Product, ProductImage


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "parent"]


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "url", "alt", "position"]


class ProductListSerializer(serializers.ModelSerializer):
    """Lean payload used by listing endpoints."""

    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "sku", "name", "slug", "price", "currency", "category_name"]


class ProductDetailSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    category = CategorySerializer(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "sku", "name", "slug", "description",
            "price", "currency", "status", "version",
            "category", "images",
            "created_at", "updated_at",
        ]
        read_only_fields = ["version"]
