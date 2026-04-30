from django.contrib.auth import authenticate, login, logout
from rest_framework import generics, permissions, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Address, Customer
from .serializers import (
    AddressSerializer,
    CustomerSerializer,
    LoginSerializer,
    RegisterSerializer,
)
from .services import register_customer


class RegisterView(APIView):
    """POST /api/v1/users/register/"""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        customer = register_customer(**s.validated_data)
        return Response(CustomerSerializer(customer).data, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    """POST /api/v1/users/login/"""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = authenticate(request, **s.validated_data)
        if user is None:
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
        login(request, user)
        return Response({"detail": "ok"})


class TokenLoginView(APIView):
    """POST /api/v1/users/token/

    Issues a DRF auth token for external clients (Postman, curl, Locust).
    Browser clients should use /login/ instead and rely on session auth.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []  # bypass session/CSRF for token issuance

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = authenticate(request, **s.validated_data)
        if user is None:
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user_id": user.id, "username": user.username})


class LogoutView(APIView):
    """POST /api/v1/users/logout/"""

    def post(self, request):
        logout(request)
        return Response({"detail": "ok"})


class MeView(generics.RetrieveAPIView):
    """GET /api/v1/users/me/"""

    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self) -> Customer:
        return self.request.user.customer


class AddressViewSet(viewsets.ModelViewSet):
    """CRUD on /api/v1/users/addresses/."""

    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Address.objects.filter(customer=self.request.user.customer)

    def perform_create(self, serializer):
        serializer.save(customer=self.request.user.customer)

    @action(detail=True, methods=["post"])
    def make_default(self, request, pk=None):
        """POST /api/v1/users/addresses/{id}/make_default/"""
        # NOTE: race-prone if user spams two clicks - flips two addresses.
        # [NFR1] candidate to wrap in distributed_lock or atomic block.
        address = self.get_object()
        Address.objects.filter(
            customer=address.customer, kind=address.kind, is_default=True
        ).update(is_default=False)
        address.is_default = True
        address.save(update_fields=["is_default"])
        return Response(AddressSerializer(address).data)
