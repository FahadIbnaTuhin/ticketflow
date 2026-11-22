from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .booking_services import BookingAccessDenied, BookingNotFound, transition_booking
from .models import Booking, BookingSeat, Payment, Refund, Seat
from .payment_providers import get_payment_provider

REFUND_CUTOFF = timedelta(hours=24)


def _event_start_datetime(event):
    local_start = datetime.combine(event.event_date, event.start_time)
    return timezone.make_aware(local_start, timezone.get_current_timezone())


def cancel_booking_with_refund(*, booking_id, customer, reason=""):
    seat_ids = list(
        BookingSeat.objects.filter(booking_id=booking_id)
        .order_by("seat_id")
        .values_list("seat_id", flat=True)
    )
    if not seat_ids:
        raise BookingNotFound("Booking does not exist.")

    with transaction.atomic():
        seats = list(
            Seat.objects.select_for_update()
            .filter(pk__in=seat_ids)
            .order_by("pk")
        )
        if len(seats) != len(set(seat_ids)):
            raise BookingNotFound("Booking seats are unavailable.")
        booking = (
            Booking.objects.select_for_update(of=("self",))
            .select_related("event")
            .filter(pk=booking_id)
            .first()
        )
        if booking is None:
            raise BookingNotFound("Booking does not exist.")
        if booking.customer_id != customer.pk:
            raise BookingAccessDenied("You do not own this booking.")

        was_confirmed = booking.status == Booking.Status.CONFIRMED
        booking = transition_booking(
            booking_id=booking.pk,
            new_status=Booking.Status.CANCELLED,
            actor=customer,
            reason=reason,
        )

        refunds = []
        now = timezone.now()
        refund_eligible = (
            was_confirmed
            and _event_start_datetime(booking.event) - now > REFUND_CUTOFF
        )
        if refund_eligible:
            payments = (
                Payment.objects.select_for_update()
                .filter(
                    booking=booking,
                    status=Payment.Status.SUCCEEDED,
                )
                .order_by("pk")
            )
            for payment in payments:
                refund, created = Refund.objects.get_or_create(
                    payment=payment,
                    defaults={
                        "amount": payment.amount,
                        "status": Refund.Status.PROCESSING,
                    },
                )
                if created:
                    provider = get_payment_provider(payment.provider)
                    result = provider.process_refund(
                        amount=refund.amount,
                        idempotency_key=f"booking-{booking.pk}:payment-{payment.pk}",
                    )
                    refund.provider_reference = result.provider_reference
                    refund.completed_at = timezone.now()
                    if result.approved:
                        refund.status = Refund.Status.SUCCEEDED
                    else:
                        refund.status = Refund.Status.FAILED
                        refund.failure_reason = (
                            result.message or "The mock provider declined the refund."
                        )
                    refund.save(
                        update_fields=(
                            "provider_reference",
                            "completed_at",
                            "status",
                            "failure_reason",
                        )
                    )
                refunds.append(refund)

        refunded_amount = sum(
            (
                refund.amount
                for refund in refunds
                if refund.status == Refund.Status.SUCCEEDED
            ),
            Decimal("0.00"),
        )
        return booking, refunded_amount > Decimal("0.00"), refunded_amount
