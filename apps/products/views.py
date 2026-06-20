"""Product views wired to NFR6 cached reads and NFR7 price updates."""
from __future__ import annotations

from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.views import APIView

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
    GET /api/v1/products/products/       -> list, cached
    GET /api/v1/products/products/{id}/  -> detail, cached
    Query params: ?category=&search=&ordering=&page=
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

    def retrieve(self, request, *args, **kwargs):
        product_id = kwargs["pk"]
        try:
            data = services.get_product_detail(int(product_id))
        except Product.DoesNotExist:
            raise NotFound(detail=f"Product {product_id} not found.")
        return Response(data)

    def list(self, request, *args, **kwargs):
        category_id = request.query_params.get("category")
        search = request.query_params.get("search")
        ordering = request.query_params.get("ordering")
        page = int(request.query_params.get("page", 1))

        try:
            data = services.list_products(
                category_id=int(category_id) if category_id else None,
                search=search or None,
                ordering=ordering or None,
                page=page,
            )
            return Response(data)
        except Exception:
            return super().list(request, *args, **kwargs)


class PriceUpdateView(APIView):
    """PATCH /api/v1/products/products/{id}/price/"""

    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk: int):
        serializer = UpdateProductPriceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            new_version = services.update_product_price(
                product_id=int(pk),
                new_price=serializer.validated_data["price"],
                expected_version=serializer.validated_data["expected_version"],
            )
        except Product.DoesNotExist:
            raise NotFound(detail=f"Product {pk} not found.")
        except StaleObjectError:
            return Response(
                {"code": "stale_product_version"},
                status=status.HTTP_409_CONFLICT,
            )

        return Response({"version": new_version})
