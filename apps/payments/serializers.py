from rest_framework import serializers

from .models import PaymentIntent


class PaymentIntentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentIntent
        fields = [
            "id", "public_id", "order", "external_id",
            "amount", "currency", "status", "version",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class CreatePaymentIntentSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()


class CapturePaymentSerializer(serializers.Serializer):
    external_id = serializers.CharField(max_length=128)


class RefundPaymentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True)
