from rest_framework.routers import DefaultRouter
from django.urls import path

from .views import CategoryViewSet, ProductViewSet, PriceUpdateView

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("products", ProductViewSet, basename="product")

urlpatterns = router.urls + [
    # NFR6 + NFR7: price update with optimistic lock + cache invalidation
    path("products/<int:pk>/price/", PriceUpdateView.as_view(), name="product-price-update"),
]
