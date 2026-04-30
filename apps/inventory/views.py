from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import services
from .models import StockItem, StockMovement
from .serializers import (
    AdjustStockSerializer,
    StockItemSerializer,
    StockMovementSerializer,
)


class StockItemViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET  /api/v1/inventory/stock-items/             -> list
    GET  /api/v1/inventory/stock-items/{id}/        -> detail
    POST /api/v1/inventory/stock-items/restock/     -> admin restock
    """

    queryset = StockItem.objects.select_related("product").all()
    serializer_class = StockItemSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def restock(self, request):
        s = AdjustStockSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.restock(**s.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/v1/inventory/movements/"""

    queryset = StockMovement.objects.all().order_by("-created_at")
    serializer_class = StockMovementSerializer
    permission_classes = [permissions.IsAdminUser]
