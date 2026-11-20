from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings


@dataclass(frozen=True)
class PaymentResult:
    approved: bool
    provider_reference: str
    message: str = ""


class PaymentProvider(Protocol):
    name: str

    def process_payment(
        self, *, amount: Decimal, idempotency_key: str
    ) -> PaymentResult: ...

    def process_refund(
        self, *, amount: Decimal, idempotency_key: str
    ) -> PaymentResult: ...


class PaymentProviderUnavailable(Exception):
    pass


class MockSuccessProvider:
    name = "mock_success"

    def process_payment(
        self, *, amount: Decimal, idempotency_key: str
    ) -> PaymentResult:
        return PaymentResult(
            approved=True,
            provider_reference=_reference(self.name, idempotency_key),
            message="Mock payment approved.",
        )

    def process_refund(
        self, *, amount: Decimal, idempotency_key: str
    ) -> PaymentResult:
        return PaymentResult(
            approved=True,
            provider_reference=_reference(self.name, f"refund:{idempotency_key}"),
            message="Mock refund approved.",
        )


class MockFailureProvider:
    name = "mock_failure"

    def process_payment(
        self, *, amount: Decimal, idempotency_key: str
    ) -> PaymentResult:
        return PaymentResult(
            approved=False,
            provider_reference=_reference(self.name, idempotency_key),
            message="Mock payment declined.",
        )

    def process_refund(
        self, *, amount: Decimal, idempotency_key: str
    ) -> PaymentResult:
        return PaymentResult(
            approved=False,
            provider_reference=_reference(self.name, f"refund:{idempotency_key}"),
            message="Mock refund declined.",
        )


def _reference(provider_name, idempotency_key):
    return str(uuid5(NAMESPACE_URL, f"ticketflow:{provider_name}:{idempotency_key}"))


def get_provider_reference(provider_name, idempotency_key):
    return _reference(provider_name, idempotency_key)


def get_payment_provider(provider_name=None):
    providers = {
        MockSuccessProvider.name: MockSuccessProvider,
        MockFailureProvider.name: MockFailureProvider,
    }
    selected_name = provider_name or settings.PAYMENT_PROVIDER
    provider_class = providers.get(selected_name)
    if provider_class is None:
        raise PaymentProviderUnavailable("The configured payment provider is unavailable.")
    if settings.DJANGO_ENV == "production":
        raise PaymentProviderUnavailable(
            "Mock payment providers are disabled in production."
        )
    return provider_class()
