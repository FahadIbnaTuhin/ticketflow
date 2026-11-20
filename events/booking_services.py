from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Booking,
    BookingSeat,
    BookingStatusHistory,
    Event,
    Seat,
    SeatHold,
)
from .pricing import calculate_seat_price

ACTIVE_BOOKING_STATUSES = (Booking.Status.HELD, Booking.Status.PENDING_PAYMENT)
VALID_TRANSITIONS = {
    Booking.Status.HELD: {
        Booking.Status.PENDING_PAYMENT,
        Booking.Status.EXPIRED,
        Booking.Status.CANCELLED,
    },
    Booking.Status.PENDING_PAYMENT: {
        Booking.Status.CONFIRMED,
        Booking.Status.EXPIRED,
        Booking.Status.CANCELLED,
    },
    Booking.Status.CONFIRMED: {Booking.Status.CANCELLED},
}


class BookingError(Exception):
    pass


class InvalidHoldSelection(BookingError):
    pass


class BookingUnavailable(BookingError):
    pass


class BookingAccessDenied(BookingError):
    pass


class InvalidBookingTransition(BookingError):
    pass


class BookingExpired(BookingError):
    pass


class BookingNotFound(BookingError):
    pass


class InvalidPricingConfiguration(BookingError):
    pass


def _require_customer(customer):
    user_model = get_user_model()
    if (
        not getattr(customer, "is_authenticated", False)
        or customer.role != user_model.Role.CUSTOMER
    ):
        raise BookingAccessDenied("Only authenticated customers can manage bookings.")


def create_booking_from_holds(*, event_id, hold_ids, customer):
    _require_customer(customer)

    try:
        requested_ids = list(hold_ids)
    except TypeError as exc:
        raise InvalidHoldSelection("Hold IDs must be a non-empty collection.") from exc
    if (
        not requested_ids
        or any(type(hold_id) is not int or hold_id < 1 for hold_id in requested_ids)
        or len(set(requested_ids)) != len(requested_ids)
    ):
        raise InvalidHoldSelection("Provide unique, positive hold IDs.")

    failure = None
    booking = None
    try:
        with transaction.atomic():
            try:
                event = Event.objects.select_for_update().get(pk=event_id)
            except Event.DoesNotExist as exc:
                raise InvalidHoldSelection("Event does not exist.") from exc
            initial_holds = list(
                SeatHold.objects.filter(
                    pk__in=requested_ids,
                    seat__event_id=event_id,
                )
                .order_by("seat_id", "pk")
                .values("pk", "seat_id")
            )
            if len(initial_holds) != len(requested_ids):
                raise InvalidHoldSelection(
                    "One or more holds do not belong to this event."
                )

            seat_ids = sorted({hold["seat_id"] for hold in initial_holds})
            seats = list(
                Seat.objects.select_for_update()
                .filter(event_id=event_id, pk__in=seat_ids)
                .order_by("pk")
            )
            if len(seats) != len(seat_ids):
                raise InvalidHoldSelection(
                    "One or more holds refer to unavailable seats."
                )

            holds = list(
                SeatHold.objects.select_for_update()
                .filter(
                    pk__in=requested_ids,
                    seat__event_id=event_id,
                )
                .select_related("seat__category")
                .order_by("seat_id", "pk")
            )
            if len(holds) != len(requested_ids):
                raise InvalidHoldSelection(
                    "One or more holds do not belong to this event."
                )

            now = timezone.now()
            expire_bookings_for_seats(seat_ids=seat_ids, now=now)
            invalid_holds = []
            for hold in holds:
                if (
                    hold.status == SeatHold.Status.ACTIVE
                    and hold.expires_at <= now
                ):
                    hold.status = SeatHold.Status.EXPIRED
                    invalid_holds.append(hold)
                    failure = InvalidHoldSelection(
                        "One or more holds have expired."
                    )
                elif (
                    hold.status != SeatHold.Status.ACTIVE
                    or hold.customer_id != customer.pk
                    or hold.expires_at <= now
                ):
                    failure = InvalidHoldSelection(
                        "All holds must be active, unexpired, and owned by you."
                    )
            if invalid_holds:
                SeatHold.objects.bulk_update(invalid_holds, ("status",))

            if failure is None:
                if BookingSeat.objects.filter(
                    seat_id__in=seat_ids,
                    occupies_seat=True,
                ).exists():
                    failure = BookingUnavailable(
                        "One or more seats are already assigned to a booking."
                    )
                elif any(hold.seat.category_id is None for hold in holds):
                    raise InvalidPricingConfiguration(
                        "Every seat must have a pricing category before booking."
                    )
                else:
                    try:
                        unit_prices = {
                            hold.pk: calculate_seat_price(
                                event,
                                category=hold.seat.category,
                                at=now,
                            )
                            for hold in holds
                        }
                    except ValidationError as exc:
                        raise InvalidPricingConfiguration(
                            "The event or seat category pricing configuration is invalid."
                        ) from exc
                    booking = Booking.objects.create(
                        customer=customer,
                        event_id=event_id,
                        status=Booking.Status.HELD,
                        created_at=now,
                        expires_at=min(hold.expires_at for hold in holds),
                    )
                    BookingSeat.objects.bulk_create(
                        [
                            BookingSeat(
                                booking=booking,
                                seat=hold.seat,
                                category=hold.seat.category,
                                category_name=hold.seat.category.name,
                                source_hold=hold,
                                unit_price=unit_prices[hold.pk],
                            )
                            for hold in holds
                        ]
                    )
                    SeatHold.objects.filter(pk__in=requested_ids).update(
                        status=SeatHold.Status.CONVERTED
                    )
                    BookingStatusHistory.objects.create(
                        booking=booking,
                        from_status="",
                        to_status=Booking.Status.HELD,
                        changed_at=now,
                        actor=customer,
                        reason="Booking created from active seat holds.",
                    )
    except IntegrityError as exc:
        raise BookingUnavailable(
            "One or more seats are no longer available for booking."
        ) from exc

    if failure is not None:
        raise failure
    return booking


def _record_transition(booking, *, new_status, actor, reason, changed_at):
    previous_status = booking.status
    booking.status = new_status
    booking.save(update_fields=("status", "updated_at"))
    if new_status in (Booking.Status.EXPIRED, Booking.Status.CANCELLED):
        booking.booking_seats.filter(occupies_seat=True).update(occupies_seat=False)
    BookingStatusHistory.objects.create(
        booking=booking,
        from_status=previous_status,
        to_status=new_status,
        changed_at=changed_at,
        actor=actor,
        reason=reason,
    )


def expire_bookings_for_seats(*, seat_ids, now):
    expired_booking_ids = list(
        BookingSeat.objects.filter(
            seat_id__in=seat_ids,
            occupies_seat=True,
            booking__status__in=ACTIVE_BOOKING_STATUSES,
            booking__expires_at__lte=now,
        )
        .order_by("booking_id")
        .values_list("booking_id", flat=True)
        .distinct()
    )
    bookings = Booking.objects.select_for_update().filter(
        pk__in=expired_booking_ids,
        status__in=ACTIVE_BOOKING_STATUSES,
        expires_at__lte=now,
    ).order_by("pk")
    for booking in bookings:
        _record_transition(
            booking,
            new_status=Booking.Status.EXPIRED,
            actor=None,
            reason="Booking reservation expired.",
            changed_at=now,
        )


def expire_due_bookings_for_customer(*, customer_id, booking_ids=None):
    now = timezone.now()
    due_bookings = Booking.objects.filter(
        customer_id=customer_id,
        status__in=ACTIVE_BOOKING_STATUSES,
        expires_at__lte=now,
    )
    if booking_ids is not None:
        due_bookings = due_bookings.filter(pk__in=booking_ids)

    for booking_id in due_bookings.order_by("pk").values_list("pk", flat=True):
        seat_ids = list(
            BookingSeat.objects.filter(
                booking_id=booking_id,
                occupies_seat=True,
            )
            .order_by("seat_id")
            .values_list("seat_id", flat=True)
        )
        with transaction.atomic():
            list(
                Seat.objects.select_for_update()
                .filter(pk__in=seat_ids)
                .order_by("pk")
            )
            booking = (
                Booking.objects.select_for_update()
                .filter(pk=booking_id, customer_id=customer_id)
                .first()
            )
            if (
                booking
                and booking.status in ACTIVE_BOOKING_STATUSES
                and booking.expires_at <= now
            ):
                _record_transition(
                    booking,
                    new_status=Booking.Status.EXPIRED,
                    actor=None,
                    reason="Booking reservation expired.",
                    changed_at=now,
                )


def transition_booking(*, booking_id, new_status, actor=None, reason=""):
    try:
        new_status = Booking.Status(new_status)
    except ValueError as exc:
        raise InvalidBookingTransition("Unknown booking status.") from exc

    if actor is not None:
        _require_customer(actor)
        if new_status in (Booking.Status.CONFIRMED, Booking.Status.EXPIRED):
            raise BookingAccessDenied(
                "Only system actions can confirm or expire a booking."
            )

    initial_seat_ids = list(
        BookingSeat.objects.filter(booking_id=booking_id)
        .order_by("seat_id")
        .values_list("seat_id", flat=True)
    )
    if not initial_seat_ids:
        raise BookingNotFound("Booking does not exist.")

    expired = False
    booking = None
    with transaction.atomic():
        seats = list(
            Seat.objects.select_for_update()
            .filter(pk__in=initial_seat_ids)
            .order_by("pk")
        )
        if len(seats) != len(set(initial_seat_ids)):
            raise BookingNotFound("Booking seats are unavailable.")
        booking = (
            Booking.objects.select_for_update()
            .filter(pk=booking_id)
            .first()
        )
        if booking is None:
            raise BookingNotFound("Booking does not exist.")
        if actor is not None and booking.customer_id != actor.pk:
            raise BookingAccessDenied("You do not own this booking.")

        list(
            BookingSeat.objects.select_for_update()
            .filter(booking=booking)
            .order_by("seat_id")
        )
        now = timezone.now()
        if (
            booking.status in ACTIVE_BOOKING_STATUSES
            and booking.expires_at <= now
        ):
            _record_transition(
                booking,
                new_status=Booking.Status.EXPIRED,
                actor=None,
                reason="Booking reservation expired.",
                changed_at=now,
            )
            expired = True
        else:
            allowed = VALID_TRANSITIONS.get(booking.status, set())
            if new_status not in allowed:
                raise InvalidBookingTransition(
                    f"Cannot transition a {booking.status} booking to {new_status}."
                )
            _record_transition(
                booking,
                new_status=new_status,
                actor=actor,
                reason=reason,
                changed_at=now,
            )

    if expired:
        raise BookingExpired("The booking has expired.")
    return booking
