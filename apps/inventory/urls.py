from rest_framework.routers import DefaultRouter

from .views import StockItemViewSet, StockMovementViewSet

router = DefaultRouter()
router.register("stock-items", StockItemViewSet, basename="stock-item")
router.register("movements", StockMovementViewSet, basename="movement")

urlpatterns = router.urls
