from django.urls import path

from .views import CartViewSet

cart_list = CartViewSet.as_view({"get": "list"})
cart_add = CartViewSet.as_view({"post": "add_item"})
cart_item = CartViewSet.as_view({"patch": "modify_item", "delete": "modify_item"})
cart_clear = CartViewSet.as_view({"post": "clear"})

urlpatterns = [
    path("", cart_list, name="cart-detail"),
    path("items/", cart_add, name="cart-add-item"),
    path("items/<int:pk>/", cart_item, name="cart-item"),
    path("clear/", cart_clear, name="cart-clear"),
]
