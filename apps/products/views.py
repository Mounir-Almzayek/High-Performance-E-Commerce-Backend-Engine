from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from core.concurrency.locks import StaleObjectError

from . import services
from .models import Category, Product
from .serializers import (
    CategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
    UpdateProductPriceSerializer,
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

    @action(
        detail=True,
        methods=["patch"],
        permission_classes=[permissions.IsAdminUser],
        url_path="price",
    )
    def price(self, request, pk=None):
        serializer = UpdateProductPriceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            product = services.update_product_price(
                product_id=int(pk),
                new_price=serializer.validated_data["price"],
                expected_version=serializer.validated_data["expected_version"],
            )
        except StaleObjectError:
            return Response(
                {"code": "stale_product_version"},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(ProductDetailSerializer(product).data)
