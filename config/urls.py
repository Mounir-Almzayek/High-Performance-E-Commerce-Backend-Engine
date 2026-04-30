from django.contrib import admin
from django.urls import include, path

api_v1 = [
    path("users/",     include("apps.users.urls")),
    path("products/",  include("apps.products.urls")),
    path("cart/",      include("apps.cart.urls")),
    path("orders/",    include("apps.orders.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("payments/",  include("apps.payments.urls")),
]

urlpatterns = [
    path("admin/",  admin.site.urls),
    path("silk/",   include("silk.urls", namespace="silk")),  # [AOP] واجهة قياس الأداء
    path("api/v1/", include((api_v1, "v1"))),
]
