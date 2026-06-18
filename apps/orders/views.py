from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.inventory.services import NotEnoughStock

from . import services
from .models import Order
from .serializers import (
    CancelOrderSerializer,
    OrderSerializer,
    PlaceOrderSerializer,
)


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET  /api/v1/orders/                 -> list user's orders
    GET  /api/v1/orders/{id}/            -> retrieve
    POST /api/v1/orders/place/           -> place from current cart
    POST /api/v1/orders/{id}/cancel/     -> cancel a pending order
    """

    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Order.objects.filter(customer=self.request.user.customer)
            .prefetch_related("items")
            .order_by("-placed_at")
        )

    @action(detail=False, methods=["post"])
    def place(self, request):
        s = PlaceOrderSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            order = services.place_order(
                customer=request.user.customer, **s.validated_data
            )
        except NotEnoughStock as exc:
            return Response(
                {"detail": str(exc), "code": "not_enough_stock"},
                status=status.HTTP_409_CONFLICT,
            )
        except services.CartEmpty as exc:
            return Response(
                {"detail": str(exc), "code": "cart_empty"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        s = CancelOrderSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        order = services.cancel_order(order=order, **s.validated_data)
        return Response(OrderSerializer(order).data)
