from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.db.models import ProtectedError
from django.urls import reverse
from django.utils import timezone

from .booking_services import (
    BookingAccessDenied,
    BookingExpired,
    BookingUnavailable,
    InvalidBookingTransition,
    InvalidHoldSelection,
    create_booking_from_holds,
    transition_booking,
)
from .models import (
    Booking,
    BookingSeat,
    BookingStatusHistory,
    Event,
    Seat,
    SeatHold,
)
from .services import create_seat_holds


@pytest.fixture
def organizer(django_user_model):
    return django_user_model.objects.create_user(
        username="booking-organizer",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )


@pytest.fixture
def customer(django_user_model):
    return django_user_model.objects.create_user(
        username="booking-customer",
        password="StrongPassword123!",
    )


@pytest.fixture
def other_customer(django_user_model):
    return django_user_model.objects.create_user(
        username="booking-other-customer",
        password="StrongPassword123!",
    )


@pytest.fixture
def event(organizer):
    return Event.objects.create(
        organizer=organizer,
        name="Booking test event",
        description="A booking test event.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 4, 15),
        start_time=time(18, 30),
        seat_map_rows=2,
        seats_per_row=3,
    )


@pytest.fixture
def held_seats(customer, event):
    seats = list(Seat.objects.filter(event=event).order_by("pk")[:2])
    holds = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk for seat in seats],
        customer=customer,
    )
    return seats, holds


@pytest.mark.django_db
def test_booking_is_created_from_all_active_holds(customer, event, held_seats):
    seats, holds = held_seats
    before = timezone.now()

    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[hold.pk for hold in holds],
        customer=customer,
    )

    assert booking.customer == customer
    assert booking.event == event
    assert booking.status == Booking.Status.HELD
    assert booking.expires_at == min(hold.expires_at for hold in holds)
    assert set(booking.booking_seats.values_list("seat_id", flat=True)) == {
        seat.pk for seat in seats
    }
    assert set(
        booking.booking_seats.values_list("source_hold__status", flat=True)
    ) == {SeatHold.Status.CONVERTED}
    assert booking.created_at >= before
    initial_history = booking.status_history.get()
    assert initial_history.from_status == ""
    assert initial_history.to_status == Booking.Status.HELD
    assert initial_history.actor == customer


@pytest.mark.django_db
def test_expired_active_hold_is_marked_expired_and_rejected(
    customer, event, monkeypatch
):
    now = timezone.now()
    monkeypatch.setattr("events.services.timezone.now", lambda: now)
    seat = Seat.objects.filter(event=event).first()
    hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]
    monkeypatch.setattr(
        "events.booking_services.timezone.now",
        lambda: hold.expires_at,
    )

    with pytest.raises(InvalidHoldSelection):
        create_booking_from_holds(
            event_id=event.pk,
            hold_ids=[hold.pk],
            customer=customer,
        )

    hold.refresh_from_db()
    assert hold.status == SeatHold.Status.EXPIRED
    assert not Booking.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hold_status",
    [SeatHold.Status.RELEASED, SeatHold.Status.EXPIRED, SeatHold.Status.CONVERTED],
)
def test_non_active_holds_are_rejected(customer, event, hold_status):
    seat = Seat.objects.filter(event=event).first()
    hold = SeatHold.objects.create(
        seat=seat,
        customer=customer,
        status=hold_status,
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    with pytest.raises(InvalidHoldSelection):
        create_booking_from_holds(
            event_id=event.pk,
            hold_ids=[hold.pk],
            customer=customer,
        )

    assert not Booking.objects.exists()


@pytest.mark.django_db
def test_wrong_owner_and_event_holds_are_rejected(
    customer, other_customer, event, organizer
):
    seat = Seat.objects.filter(event=event).first()
    hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]
    other_event = Event.objects.create(
        organizer=organizer,
        name="Other booking event",
        description="Another event.",
        venue="Other Hall",
        city="Dhaka",
        event_date=date(2027, 5, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=1,
    )

    with pytest.raises(InvalidHoldSelection):
        create_booking_from_holds(
            event_id=event.pk,
            hold_ids=[hold.pk],
            customer=other_customer,
        )
    with pytest.raises(InvalidHoldSelection):
        create_booking_from_holds(
            event_id=other_event.pk,
            hold_ids=[hold.pk],
            customer=customer,
        )
    assert not Booking.objects.exists()


@pytest.mark.django_db
def test_multiple_hold_conversion_is_all_or_nothing(
    customer, other_customer, event
):
    seats = list(Seat.objects.filter(event=event).order_by("pk")[:2])
    own_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seats[0].pk],
        customer=customer,
    )[0]
    other_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seats[1].pk],
        customer=other_customer,
    )[0]

    with pytest.raises(InvalidHoldSelection):
        create_booking_from_holds(
            event_id=event.pk,
            hold_ids=[own_hold.pk, other_hold.pk],
            customer=customer,
        )

    own_hold.refresh_from_db()
    other_hold.refresh_from_db()
    assert own_hold.status == SeatHold.Status.ACTIVE
    assert other_hold.status == SeatHold.Status.ACTIVE
    assert not Booking.objects.exists()
    assert not BookingSeat.objects.exists()


@pytest.mark.django_db
def test_duplicate_booking_seat_is_rejected_by_database(
    customer, event, held_seats
):
    _, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )
    existing = booking.booking_seats.get()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            BookingSeat.objects.create(
                booking=booking,
                seat=existing.seat,
                source_hold=holds[1],
            )

    assert BookingSeat.objects.filter(seat=existing.seat, occupies_seat=True).count() == 1


@pytest.mark.django_db
def test_cancelling_booking_releases_seats_and_records_history(
    customer, other_customer, event, held_seats
):
    seats, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )

    cancelled = transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.CANCELLED,
        actor=customer,
        reason="Customer requested cancellation.",
    )
    replacement_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seats[0].pk],
        customer=other_customer,
    )[0]

    assert cancelled.status == Booking.Status.CANCELLED
    assert not BookingSeat.objects.get(booking=booking).occupies_seat
    assert replacement_hold.seat == seats[0]
    history = booking.status_history.get(to_status=Booking.Status.CANCELLED)
    assert history.from_status == Booking.Status.HELD
    assert history.actor == customer
    assert history.reason == "Customer requested cancellation."


@pytest.mark.django_db
def test_expired_booking_cannot_be_confirmed_and_releases_seats(
    customer, other_customer, event, held_seats
):
    seats, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )
    now = timezone.now()
    Booking.objects.filter(pk=booking.pk).update(
        created_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
    )

    with pytest.raises(BookingExpired):
        transition_booking(
            booking_id=booking.pk,
            new_status=Booking.Status.CONFIRMED,
        )

    booking.refresh_from_db()
    assert booking.status == Booking.Status.EXPIRED
    assert not booking.booking_seats.get().occupies_seat
    replacement_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seats[0].pk],
        customer=other_customer,
    )[0]
    assert replacement_hold.seat == seats[0]


@pytest.mark.django_db
def test_booking_status_transitions_are_enforced(customer, event, held_seats):
    _, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )

    with pytest.raises(BookingAccessDenied):
        transition_booking(
            booking_id=booking.pk,
            new_status=Booking.Status.CONFIRMED,
            actor=customer,
        )
    with pytest.raises(InvalidBookingTransition):
        transition_booking(
            booking_id=booking.pk,
            new_status=Booking.Status.CONFIRMED,
        )

    pending = transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.PENDING_PAYMENT,
        actor=customer,
        reason="Payment initiated.",
    )
    confirmed = transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.CONFIRMED,
        reason="Payment provider confirmed.",
    )
    assert pending.status == Booking.Status.PENDING_PAYMENT
    assert confirmed.status == Booking.Status.CONFIRMED
    assert confirmed.booking_seats.filter(occupies_seat=True).count() == 1

    with pytest.raises(InvalidBookingTransition):
        transition_booking(
            booking_id=booking.pk,
            new_status=Booking.Status.HELD,
            actor=customer,
        )

    history = list(booking.status_history.values_list("from_status", "to_status"))
    assert history == [
        ("", Booking.Status.HELD),
        (Booking.Status.HELD, Booking.Status.PENDING_PAYMENT),
        (Booking.Status.PENDING_PAYMENT, Booking.Status.CONFIRMED),
    ]


@pytest.mark.django_db
def test_booking_owner_endpoints_are_scoped_to_customer(
    client, customer, other_customer, event, held_seats
):
    _, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )
    client.force_login(other_customer)

    list_response = client.get(reverse("events:booking-list"))
    detail_response = client.get(
        reverse("events:booking-detail", args=[booking.pk])
    )
    cancel_response = client.post(
        reverse("events:booking-cancel", args=[booking.pk]),
        {"reason": "Not my booking"},
    )

    assert list_response.status_code == 200
    assert list_response.json() == []
    assert detail_response.status_code == 404
    assert cancel_response.status_code == 404
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", ["list", "detail"])
def test_booking_reads_lazily_expire_overdue_reservations(
    client, customer, other_customer, event, held_seats, endpoint
):
    seats, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )
    now = timezone.now()
    Booking.objects.filter(pk=booking.pk).update(
        created_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
    )
    client.force_login(customer)

    if endpoint == "list":
        response = client.get(reverse("events:booking-list"))
        serialized = response.json()[0]
    else:
        response = client.get(
            reverse("events:booking-detail", args=[booking.pk])
        )
        serialized = response.json()

    booking.refresh_from_db()
    assert response.status_code == 200
    assert serialized["status"] == Booking.Status.EXPIRED
    assert booking.status == Booking.Status.EXPIRED
    assert not booking.booking_seats.get().occupies_seat

    replacement_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seats[0].pk],
        customer=other_customer,
    )[0]
    assert replacement_hold.seat == seats[0]


@pytest.mark.django_db
def test_booking_and_event_cannot_be_deleted_after_booking_exists(
    customer, event, held_seats
):
    _, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )

    with pytest.raises(ProtectedError):
        booking.delete()
    with pytest.raises(ProtectedError):
        event.delete()

    assert Booking.objects.filter(pk=booking.pk).exists()
    assert BookingStatusHistory.objects.filter(booking=booking).exists()


@pytest.mark.django_db
def test_booking_endpoint_converts_holds(client, customer, event, held_seats):
    _, holds = held_seats
    client.force_login(customer)

    response = client.post(
        reverse("events:booking-create", args=[event.pk]),
        {
            "hold_ids": [hold.pk for hold in holds],
            "unit_price": "0.01",
            "total_price": "0.01",
        },
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["status"] == Booking.Status.HELD
    assert len(response.json()["seats"]) == len(holds)
    assert [seat["unit_price"] for seat in response.json()["seats"]] == [
        "0.00",
        "0.00",
    ]
    assert response.json()["total_price"] == "0.00"
    assert len(response.json()["history"]) == 1


@pytest.mark.django_db
def test_cancel_endpoint_rejects_non_object_reason(client, customer, event, held_seats):
    _, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )
    client.force_login(customer)

    response = client.post(
        reverse("events:booking-cancel", args=[booking.pk]),
        [{"reason": "malformed"}],
        content_type="application/json",
    )

    assert response.status_code == 400
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD


@pytest.mark.django_db
def test_hold_endpoint_cannot_hold_a_booked_seat(
    customer, other_customer, event, held_seats
):
    seats, holds = held_seats
    create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )

    from .services import SeatUnavailable

    with pytest.raises(SeatUnavailable):
        create_seat_holds(
            event_id=event.pk,
            seat_ids=[seats[0].pk],
            customer=other_customer,
        )


@pytest.mark.django_db
def test_expired_booking_is_released_when_another_customer_requests_hold(
    customer, other_customer, event, held_seats
):
    seats, holds = held_seats
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[holds[0].pk],
        customer=customer,
    )
    now = timezone.now()
    Booking.objects.filter(pk=booking.pk).update(
        created_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(seconds=1),
    )

    new_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seats[0].pk],
        customer=other_customer,
    )[0]

    booking.refresh_from_db()
    assert booking.status == Booking.Status.EXPIRED
    assert new_hold.seat == seats[0]


@pytest.mark.django_db
def test_organizer_cannot_create_booking_from_customer_holds(organizer, event):
    seat = Seat.objects.filter(event=event).first()
    with pytest.raises(BookingAccessDenied):
        create_booking_from_holds(
            event_id=event.pk,
            hold_ids=[seat.pk],
            customer=organizer,
        )


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Row-level concurrency behavior must be tested on PostgreSQL.",
)
@pytest.mark.django_db(transaction=True)
def test_concurrent_conversion_of_same_hold_creates_one_booking(
    customer, other_customer, event
):
    seat = Seat.objects.filter(event=event).first()
    hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]
    barrier = Barrier(2)

    def convert(customer_id):
        connections.close_all()
        try:
            user = get_user_model().objects.get(pk=customer_id)
            barrier.wait(timeout=10)
            try:
                booking = create_booking_from_holds(
                    event_id=event.pk,
                    hold_ids=[hold.pk],
                    customer=user,
                )
                return ("created", booking.pk)
            except (InvalidHoldSelection, BookingUnavailable):
                return ("rejected", None)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(convert, [customer.pk, other_customer.pk]))

    assert sorted(result[0] for result in results) == ["created", "rejected"]
    assert Booking.objects.filter(
        event=event,
        booking_seats__seat=seat,
        booking_seats__occupies_seat=True,
    ).count() == 1
