from rest_framework import permissions, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter

from .models import Category, Product
from .serializers import (
    CategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
)


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/v1/products/categories/"""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.AllowAny]


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/products/products/                 -> list
    GET /api/v1/products/products/{id}/            -> detail
    Query params: ?category=&search=&ordering=
    """

    permission_classes = [permissions.AllowAny]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["name", "sku", "description"]
    ordering_fields = ["price", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = Product.objects.filter(status=Product.ACTIVE).select_related("category")
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category_id=category)
        return qs

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ProductDetailSerializer
        return ProductListSerializer
