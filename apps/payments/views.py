from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orders.models import Order

from . import services
from .models import PaymentIntent
from .serializers import (
    CapturePaymentSerializer,
    CreatePaymentIntentSerializer,
    PaymentIntentSerializer,
    RefundPaymentSerializer,
)


class PaymentIntentViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET  /api/v1/payments/intents/                    -> list
    GET  /api/v1/payments/intents/{id}/               -> retrieve
    POST /api/v1/payments/intents/                    -> create intent
    POST /api/v1/payments/intents/{id}/capture/       -> capture
    POST /api/v1/payments/intents/{id}/refund/        -> refund
    """

    serializer_class = PaymentIntentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PaymentIntent.objects.filter(
            order__customer=self.request.user.customer
        ).order_by("-created_at")

    def create(self, request):
        s = CreatePaymentIntentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        order = Order.objects.get(
            pk=s.validated_data["order_id"],
            customer=request.user.customer,
        )
        intent = services.create_intent(
            order_id=order.id, amount=order.total, currency=order.currency
        )
        return Response(PaymentIntentSerializer(intent).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def capture(self, request, pk=None):
        s = CapturePaymentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        intent = services.capture_payment(intent_id=int(pk), **s.validated_data)
        return Response(PaymentIntentSerializer(intent).data)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        s = RefundPaymentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        intent = services.refund_payment(intent_id=int(pk), **s.validated_data)
        return Response(PaymentIntentSerializer(intent).data)


class WebhookView(APIView):
    """POST /api/v1/payments/webhook/

    Inbound endpoint for the payment gateway. Must be idempotent on the
    request signature (replayed events are common).
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        signature = request.headers.get("X-Gateway-Signature", "")
        services.process_webhook(signature, request.data)
        return Response(status=status.HTTP_204_NO_CONTENT)
