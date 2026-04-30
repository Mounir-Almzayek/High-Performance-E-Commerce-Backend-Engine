from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AddressViewSet,
    LoginView,
    LogoutView,
    MeView,
    RegisterView,
    TokenLoginView,
)

router = DefaultRouter()
router.register("addresses", AddressViewSet, basename="address")

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/", TokenLoginView.as_view(), name="token"),
    path("me/", MeView.as_view(), name="me"),
    path("", include(router.urls)),
]
