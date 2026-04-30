from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import services
from .models import Cart, CartItem
from .serializers import (
    AddItemSerializer,
    CartItemSerializer,
    CartSerializer,
    UpdateItemSerializer,
)


class CartViewSet(viewsets.GenericViewSet):
    """
    GET    /api/v1/cart/                   -> retrieve current user's cart
    POST   /api/v1/cart/items/             -> add item
    PATCH  /api/v1/cart/items/{id}/        -> update quantity
    DELETE /api/v1/cart/items/{id}/        -> remove
    POST   /api/v1/cart/clear/             -> empty cart
    """

    serializer_class = CartSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        cart = services.get_or_create_cart(request.user.customer)
        return Response(CartSerializer(cart).data)

    @action(detail=False, methods=["post"], url_path="items")
    def add_item(self, request):
        s = AddItemSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        item = services.add_item(customer=request.user.customer, **s.validated_data)
        return Response(CartItemSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch", "delete"], url_path="items")
    def modify_item(self, request, pk=None):
        item = CartItem.objects.get(pk=pk, cart__customer=request.user.customer)
        if request.method == "DELETE":
            item.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        s = UpdateItemSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        updated = services.update_item(
            customer=request.user.customer, item_id=item.id, **s.validated_data
        )
        if updated is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(CartItemSerializer(updated).data)

    @action(detail=False, methods=["post"])
    def clear(self, request):
        services.clear_cart(customer=request.user.customer)
        return Response(status=status.HTTP_204_NO_CONTENT)
