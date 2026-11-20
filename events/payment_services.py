from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .booking_services import (
    BookingAccessDenied,
    BookingExpired,
    BookingNotFound,
    InvalidBookingTransition,
    expire_due_bookings_for_customer,
    transition_booking,
)
from .models import Booking, BookingSeat, Payment, Seat
from .payment_providers import (
    PaymentProviderUnavailable,
    get_provider_reference,
    get_payment_provider,
)

PAYMENT_PROCESSING_LEASE = timedelta(seconds=30)


class InvalidIdempotencyKey(ValueError):
    pass


class PaymentRequestConflict(Exception):
    pass


def initiate_booking_payment(*, booking_id, customer, idempotency_key):
    user_model = get_user_model()
    if (
        not getattr(customer, "is_authenticated", False)
        or customer.role != user_model.Role.CUSTOMER
    ):
        raise BookingAccessDenied("Only authenticated customers can pay for bookings.")
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 255
    ):
        raise InvalidIdempotencyKey(
            "Provide a non-empty Idempotency-Key header of at most 255 characters."
        )
    idempotency_key = idempotency_key.strip()

    expire_due_bookings_for_customer(
        customer_id=customer.pk,
        booking_ids=[booking_id],
    )
    seat_ids = list(
        BookingSeat.objects.filter(booking_id=booking_id)
        .order_by("seat_id")
        .values_list("seat_id", flat=True)
    )
    if not seat_ids:
        raise BookingNotFound("Booking does not exist.")

    payment = None
    expired = False
    with transaction.atomic():
        seats = list(
            Seat.objects.select_for_update()
            .filter(pk__in=seat_ids)
            .order_by("pk")
        )
        if len(seats) != len(set(seat_ids)):
            raise BookingNotFound("Booking seats are unavailable.")
        booking = Booking.objects.select_for_update().filter(pk=booking_id).first()
        if booking is None:
            raise BookingNotFound("Booking does not exist.")
        if booking.customer_id != customer.pk:
            raise BookingAccessDenied("You do not own this booking.")

        payment = (
            Payment.objects.select_for_update()
            .filter(
                booking=booking,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        now = timezone.now()
        if payment is not None and payment.status != Payment.Status.PROCESSING:
            return payment
        if payment is not None:
            provider = get_payment_provider(payment.provider)
            lease_expired = (
                payment.processing_started_at is None
                or now - payment.processing_started_at >= PAYMENT_PROCESSING_LEASE
            )
            if not lease_expired:
                return payment
            if booking.status not in (
                Booking.Status.HELD,
                Booking.Status.PENDING_PAYMENT,
            ) or booking.expires_at <= now:
                payment.status = Payment.Status.FAILED
                payment.failure_reason = "The booking is no longer available for payment."
                payment.completed_at = now
                payment.save(
                    update_fields=(
                        "status",
                        "failure_reason",
                        "completed_at",
                        "updated_at",
                    )
                )
                return payment
            payment.processing_started_at = now
            payment.save(update_fields=("processing_started_at", "updated_at"))
        elif (
            booking.status in (Booking.Status.HELD, Booking.Status.PENDING_PAYMENT)
            and booking.expires_at <= now
        ):
            expired = True
        elif booking.status not in (
            Booking.Status.HELD,
            Booking.Status.PENDING_PAYMENT,
        ):
            raise InvalidBookingTransition(
                f"Cannot initiate payment for a {booking.status} booking."
            )
        elif Payment.objects.filter(
            booking=booking,
            status=Payment.Status.PROCESSING,
        ).exists():
            raise PaymentRequestConflict(
                "Another payment request for this booking is still processing."
            )
        else:
            provider = get_payment_provider()
            if booking.status == Booking.Status.HELD:
                try:
                    booking = transition_booking(
                        booking_id=booking.pk,
                        new_status=Booking.Status.PENDING_PAYMENT,
                        actor=customer,
                        reason="Customer initiated payment.",
                    )
                except BookingExpired:
                    expired = True
            if not expired:
                payment = Payment.objects.create(
                    booking=booking,
                    provider=provider.name,
                    idempotency_key=idempotency_key,
                    amount=booking.total_price,
                    provider_reference=get_provider_reference(
                        provider.name,
                        f"booking-{booking_id}:{idempotency_key}",
                    ),
                    processing_started_at=now,
                )

    if expired:
        expire_due_bookings_for_customer(
            customer_id=customer.pk,
            booking_ids=[booking_id],
        )
        raise BookingExpired("The booking has expired.")

    try:
        result = provider.process_payment(
            amount=payment.amount,
            idempotency_key=f"booking-{booking_id}:{idempotency_key}",
        )
    except Exception as exc:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment.pk)
            payment.status = Payment.Status.FAILED
            if isinstance(exc, PaymentProviderUnavailable):
                payment.failure_reason = (
                    "The configured payment provider is unavailable."
                )
            else:
                payment.failure_reason = "The payment provider failed unexpectedly."
            payment.completed_at = timezone.now()
            payment.save(
                update_fields=("status", "failure_reason", "completed_at", "updated_at")
            )
        raise PaymentProviderUnavailable(
            "The payment provider is unavailable."
        ) from exc

    booking_expired = False
    with transaction.atomic():
        list(
            Seat.objects.select_for_update()
            .filter(pk__in=seat_ids)
            .order_by("pk")
        )
        booking = Booking.objects.select_for_update().get(pk=booking_id)
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status != Payment.Status.PROCESSING:
            return payment

        now = timezone.now()
        if booking.expires_at <= now and booking.status in (
            Booking.Status.HELD,
            Booking.Status.PENDING_PAYMENT,
        ):
            booking_expired = True
            payment.status = Payment.Status.FAILED
            payment.failure_reason = (
                "The booking expired before payment could be confirmed."
            )
        elif (
            booking.status != Booking.Status.PENDING_PAYMENT
            or not result.approved
        ):
            payment.status = Payment.Status.FAILED
            if not result.approved:
                payment.failure_reason = (
                    result.message or "The payment provider declined the payment."
                )
            else:
                payment.failure_reason = "The booking is no longer available for payment."
        else:
            try:
                transition_booking(
                    booking_id=booking.pk,
                    new_status=Booking.Status.CONFIRMED,
                    reason="Payment approved by the mock provider.",
                )
                payment.status = Payment.Status.SUCCEEDED
            except (BookingExpired, InvalidBookingTransition):
                booking_expired = booking.expires_at <= timezone.now()
                payment.status = Payment.Status.FAILED
                payment.failure_reason = (
                    "The booking expired before payment could be confirmed."
                    if booking_expired
                    else "The booking is no longer available for payment."
                )

        payment.provider_reference = result.provider_reference
        payment.completed_at = timezone.now()
        payment.save(
            update_fields=(
                "status",
                "failure_reason",
                "provider_reference",
                "completed_at",
                "updated_at",
            )
        )

    if booking_expired:
        expire_due_bookings_for_customer(
            customer_id=customer.pk,
            booking_ids=[booking_id],
        )
    return payment
