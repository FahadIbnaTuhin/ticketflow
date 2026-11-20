from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .booking_services import expire_bookings_for_seats
from .models import BookingSeat, Seat, SeatHold

HOLD_DURATION = timedelta(minutes=5)


class InvalidSeatSelection(ValueError):
    pass


class SeatUnavailable(ValueError):
    pass


class CustomerRequired(PermissionError):
    pass


def create_seat_holds(*, event_id, seat_ids, customer):
    user_model = get_user_model()
    if (
        not getattr(customer, "is_authenticated", False)
        or customer.role != user_model.Role.CUSTOMER
    ):
        raise CustomerRequired("Only authenticated customers can hold seats.")

    try:
        requested_ids = list(seat_ids)
    except TypeError as exc:
        raise InvalidSeatSelection("Seat IDs must be a non-empty collection.") from exc
    if not requested_ids or any(
        type(seat_id) is not int or seat_id < 1 for seat_id in requested_ids
    ):
        raise InvalidSeatSelection("Seat IDs must be positive integers.")
    requested_ids = sorted(set(requested_ids))

    try:
        unavailable = False
        holds = []
        with transaction.atomic():
            seats = list(
                Seat.objects.select_for_update()
                .filter(event_id=event_id, pk__in=requested_ids)
                .order_by("pk")
            )
            if len(seats) != len(requested_ids):
                raise InvalidSeatSelection(
                    "One or more seats do not belong to this event."
                )

            now = timezone.now()
            seat_ids_in_order = [seat.pk for seat in seats]
            expire_bookings_for_seats(seat_ids=seat_ids_in_order, now=now)
            SeatHold.objects.filter(
                seat_id__in=seat_ids_in_order,
                status=SeatHold.Status.ACTIVE,
                expires_at__lte=now,
            ).update(status=SeatHold.Status.EXPIRED)

            active_holds = list(
                SeatHold.objects.filter(
                    seat_id__in=seat_ids_in_order,
                    status=SeatHold.Status.ACTIVE,
                    expires_at__gt=now,
                ).select_related("seat")
            )
            if any(hold.customer_id != customer.pk for hold in active_holds):
                unavailable = True
            elif BookingSeat.objects.filter(
                seat_id__in=seat_ids_in_order,
                occupies_seat=True,
            ).exists():
                unavailable = True
            else:
                held_seat_ids = {hold.seat_id for hold in active_holds}
                new_holds = [
                    SeatHold(
                        seat_id=seat_id,
                        customer=customer,
                        status=SeatHold.Status.ACTIVE,
                        created_at=now,
                        expires_at=now + HOLD_DURATION,
                    )
                    for seat_id in seat_ids_in_order
                    if seat_id not in held_seat_ids
                ]
                if new_holds:
                    SeatHold.objects.bulk_create(new_holds)
                holds = list(
                    SeatHold.objects.filter(
                        seat_id__in=seat_ids_in_order,
                        customer=customer,
                        status=SeatHold.Status.ACTIVE,
                        expires_at__gt=now,
                    )
                    .select_related("seat")
                    .order_by("seat_id")
                )
    except IntegrityError as exc:
        raise SeatUnavailable("One or more seats are no longer available.") from exc

    if unavailable:
        raise SeatUnavailable("One or more seats are already held.")
    return holds
