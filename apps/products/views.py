"""
Product views — NFR6 (distributed cache integration).

ViewSet changes:
  - ProductViewSet.retrieve() delegates to services.get_product_detail(),
    which is the cache read-through entry point. The DB is only hit on a
    cold miss or after a soft-TTL expiry.
  - ProductViewSet.list() delegates to services.list_products(), which
    caches per (filter, page) hash.
  - PriceUpdateView provides a PATCH endpoint that triggers an optimistic-
    locked price update (NFR7) and schedules cache invalidation (NFR6).

All other behaviour (authentication, filtering, DRF pagination on the
list path for direct DB fallback) is preserved from the original.
"""
from __future__ import annotations

from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework.views import APIView

from core.concurrency.locks import StaleObjectError

from .models import Category, Product
from .serializers import (
    CategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
)
from . import services


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/v1/products/categories/"""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.AllowAny]


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/products/products/          -> list  (cached)
    GET /api/v1/products/products/{id}/     -> detail (cached)
    Query params: ?category=&search=&ordering=&page=
    """

    permission_classes = [permissions.AllowAny]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["name", "sku", "description"]
    ordering_fields = ["price", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        # Kept for DRF router introspection (schema generation, admin, etc.).
        # The actual query runs inside service helpers when caching is active.
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
        """
        Return a cached product detail response.

        On a cache hit, the serialised dict is returned directly without
        touching the DB. The view re-wraps it in a DRF Response so the
        content negotiation / renderer pipeline is unchanged.
        """
        product_id = kwargs["pk"]
        try:
            data = services.get_product_detail(int(product_id))
        except Product.DoesNotExist:
            raise NotFound(detail=f"Product {product_id} not found.")
        return Response(data)

    def list(self, request, *args, **kwargs):
        """
        Return a cached product listing.

        Falls back to the parent implementation (DB query with DRF
        pagination) when any exception occurs in the service, so a Redis
        outage does not take down the catalogue.
        """
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
        except Exception:  # noqa: BLE001 — Redis unavailable → fall back
            return super().list(request, *args, **kwargs)


class PriceUpdateView(APIView):
    """
    PATCH /api/v1/products/products/{id}/price/

    Updates the product price using an optimistic-locked write (NFR7) and
    schedules cache invalidation on commit (NFR6).

    Request body:
        {
            "new_price": "19.99",
            "expected_version": 4
        }

    Responses:
        200 OK     — price updated; returns {"version": <new_version>}
        409 Conflict — optimistic lock conflict; client should re-read
        404 Not Found — product not found
    """

    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk: int):
        new_price = request.data.get("new_price")
        expected_version = request.data.get("expected_version")

        if new_price is None or expected_version is None:
            return Response(
                {"detail": "Both 'new_price' and 'expected_version' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            new_version = services.update_product_price(
                product_id=int(pk),
                new_price=new_price,
                expected_version=int(expected_version),
            )
            return Response({"version": new_version})
        except Product.DoesNotExist:
            raise NotFound(detail=f"Product {pk} not found.")
        except StaleObjectError:
            return Response(
                {"detail": "Optimistic lock conflict. Re-read the product and retry."},
                status=status.HTTP_409_CONFLICT,
            )
