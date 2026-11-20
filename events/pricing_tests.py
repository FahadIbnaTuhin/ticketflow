from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from .booking_services import create_booking_from_holds
from .models import BookingSeat, Event, Seat, SeatHold
from .pricing import calculate_seat_price
from .services import create_seat_holds


@pytest.fixture
def pricing_event(django_user_model):
    organizer = django_user_model.objects.create_user(
        username="pricing-organizer",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )
    return Event(
        organizer=organizer,
        name="Pricing event",
        description="An event used for pricing tests.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 6, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=2,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("100.00"),
    )


def aware_datetime(day, hour, minute=0):
    value = datetime(2027, 6, day, hour, minute)
    return timezone.make_aware(value, timezone.get_current_timezone())


def test_base_strategy_uses_configured_base_price(pricing_event):
    at = aware_datetime(1, 12)

    assert calculate_seat_price(pricing_event, at=at) == Decimal("100.00")


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        ("start", Decimal("75.00")),
        ("within", Decimal("75.00")),
        ("end", Decimal("100.00")),
        ("after", Decimal("100.00")),
    ],
)
def test_early_bird_strategy_respects_inclusive_start_exclusive_end(
    pricing_event, at, expected
):
    pricing_event.pricing_strategy = Event.PricingStrategy.EARLY_BIRD
    pricing_event.early_bird_price = Decimal("75.00")
    pricing_event.early_bird_starts_at = aware_datetime(1, 9)
    pricing_event.early_bird_ends_at = aware_datetime(10, 9)
    times = {
        "start": aware_datetime(1, 9),
        "within": aware_datetime(5, 12),
        "end": aware_datetime(10, 9),
        "after": aware_datetime(10, 9) + timedelta(seconds=1),
    }

    assert calculate_seat_price(pricing_event, at=times[at]) == expected


@pytest.mark.parametrize(
    "updates",
    [
        {"early_bird_price": Decimal("75.00")},
        {
            "early_bird_price": Decimal("75.00"),
            "early_bird_starts_at": aware_datetime(1, 9),
        },
        {
            "early_bird_price": Decimal("-1.00"),
            "early_bird_starts_at": aware_datetime(1, 9),
            "early_bird_ends_at": aware_datetime(10, 9),
        },
        {
            "early_bird_price": Decimal("101.00"),
            "early_bird_starts_at": aware_datetime(1, 9),
            "early_bird_ends_at": aware_datetime(10, 9),
        },
        {
            "early_bird_price": Decimal("75.00"),
            "early_bird_starts_at": aware_datetime(10, 9),
            "early_bird_ends_at": aware_datetime(1, 9),
        },
        {
            "early_bird_price": Decimal("75.00"),
            "early_bird_starts_at": aware_datetime(1, 9),
            "early_bird_ends_at": aware_datetime(15, 18, 30),
        },
        {"base_price": Decimal("-0.01")},
    ],
)
def test_invalid_early_bird_configuration_fails_validation(pricing_event, updates):
    pricing_event.pricing_strategy = Event.PricingStrategy.EARLY_BIRD
    for field, value in updates.items():
        setattr(pricing_event, field, value)

    with pytest.raises(ValidationError):
        pricing_event.full_clean()


@pytest.mark.django_db
def test_booking_price_snapshots_are_not_changed_by_later_event_price_updates(
    django_user_model, pricing_event
):
    customer = django_user_model.objects.create_user(
        username="pricing-customer",
        password="StrongPassword123!",
    )
    pricing_event.save()
    seat = Seat.objects.filter(event=pricing_event).first()
    hold = create_seat_holds(
        event_id=pricing_event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]
    booking = create_booking_from_holds(
        event_id=pricing_event.pk,
        hold_ids=[hold.pk],
        customer=customer,
    )
    booking_seat = BookingSeat.objects.get(booking=booking)

    Event.objects.filter(pk=pricing_event.pk).update(base_price=Decimal("250.00"))

    booking.refresh_from_db()
    booking_seat.refresh_from_db()
    assert booking_seat.unit_price == Decimal("100.00")
    assert booking.total_price == Decimal("100.00")


@pytest.mark.django_db
def test_booking_seat_snapshot_cannot_be_changed_with_model_save(
    django_user_model, pricing_event
):
    customer = django_user_model.objects.create_user(
        username="pricing-customer-immutable",
        password="StrongPassword123!",
    )
    pricing_event.save()
    seat = Seat.objects.filter(event=pricing_event).first()
    hold = SeatHold.objects.create(
        seat=seat,
        customer=customer,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    booking = create_booking_from_holds(
        event_id=pricing_event.pk,
        hold_ids=[hold.pk],
        customer=customer,
    )
    booking_seat = BookingSeat.objects.get(booking=booking)
    booking_seat.unit_price = Decimal("1.00")

    with pytest.raises(ValidationError, match="immutable"):
        booking_seat.save()


@pytest.mark.django_db
def test_booking_api_exposes_snapshot_prices_and_total(
    client, django_user_model, pricing_event
):
    customer = django_user_model.objects.create_user(
        username="pricing-api-customer",
        password="StrongPassword123!",
    )
    pricing_event.pricing_strategy = Event.PricingStrategy.EARLY_BIRD
    pricing_event.early_bird_price = Decimal("75.00")
    pricing_event.early_bird_starts_at = timezone.now() - timedelta(minutes=1)
    pricing_event.early_bird_ends_at = timezone.now() + timedelta(days=1)
    pricing_event.save()
    seats = list(Seat.objects.filter(event=pricing_event).order_by("pk"))
    holds = create_seat_holds(
        event_id=pricing_event.pk,
        seat_ids=[seat.pk for seat in seats],
        customer=customer,
    )
    client.force_login(customer)

    response = client.post(
        reverse("events:booking-create", args=[pricing_event.pk]),
        {"hold_ids": [hold.pk for hold in holds]},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert [seat["unit_price"] for seat in response.json()["seats"]] == [
        "75.00",
        "75.00",
    ]
    assert response.json()["total_price"] == "150.00"
