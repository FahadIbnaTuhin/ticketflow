from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from .booking_services import create_booking_from_holds
from .models import Booking, BookingSeat, Event, EventCategory, Seat
from .pricing import calculate_seat_price
from .services import create_seat_holds


@pytest.fixture
def category_event(django_user_model):
    organizer = django_user_model.objects.create_user(
        username="category-organizer",
        password="test-password",
        role=django_user_model.Role.ORGANIZER,
    )
    event = Event.objects.create(
        organizer=organizer,
        name="Category event",
        description="An event used for category tests.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 8, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=2,
        base_price=Decimal("100.00"),
    )
    return event


@pytest.fixture
def customer(django_user_model):
    return django_user_model.objects.create_user(
        username="category-customer",
        password="test-password",
    )


@pytest.mark.django_db
def test_organizer_can_create_edit_and_assign_event_categories(
    client, category_event, django_user_model
):
    organizer = category_event.organizer
    client.force_login(organizer)
    create_response = client.post(
        reverse("events:category-create", args=[category_event.pk]),
        {
            "name": "VIP",
            "base_price": "250.00",
            "early_bird_price": "",
        },
    )

    assert create_response.status_code == 302
    category = EventCategory.objects.get(event=category_event, name="VIP")
    edit_response = client.post(
        reverse(
            "events:category-update",
            args=[category_event.pk, category.pk],
        ),
        {
            "name": "Premium",
            "base_price": "275.00",
            "early_bird_price": "",
        },
    )
    assert edit_response.status_code == 302
    category.refresh_from_db()
    assert category.name == "Premium"
    assert category.base_price == Decimal("275.00")

    seat = Seat.objects.filter(event=category_event).first()
    assign_response = client.post(
        reverse(
            "events:seat-category-assign",
            args=[category_event.pk, seat.pk],
        ),
        {"category_id": category.pk},
    )

    assert assign_response.status_code == 302
    seat.refresh_from_db()
    assert seat.category == category


@pytest.mark.django_db
def test_category_management_is_limited_to_event_organizer(
    client, category_event, django_user_model
):
    other_organizer = django_user_model.objects.create_user(
        username="other-category-organizer",
        password="test-password",
        role=django_user_model.Role.ORGANIZER,
    )
    category = category_event.categories.get(name="General")
    client.force_login(other_organizer)

    response = client.get(
        reverse("events:category-list", args=[category_event.pk])
    )
    update_response = client.post(
        reverse(
            "events:category-update",
            args=[category_event.pk, category.pk],
        ),
        {
            "name": "Changed",
            "base_price": "1.00",
        },
    )

    assert response.status_code == 404
    assert update_response.status_code == 404
    category.refresh_from_db()
    assert category.name == "General"


@pytest.mark.django_db
def test_customer_cannot_manage_categories(client, category_event, customer):
    client.force_login(customer)

    response = client.get(
        reverse("events:category-list", args=[category_event.pk])
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_seat_rejects_category_from_another_event(category_event, django_user_model):
    other_event = Event.objects.create(
        organizer=category_event.organizer,
        name="Other category event",
        description="Another event.",
        venue="Other Hall",
        city="Dhaka",
        event_date=date(2027, 9, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=1,
    )
    seat = category_event.seats.first()
    seat.category = other_event.categories.get(name="General")

    with pytest.raises(ValidationError, match="same event"):
        seat.save(update_fields=("category",))


@pytest.mark.django_db
def test_seat_assignment_rejects_category_from_another_event(
    client, category_event, django_user_model
):
    other_event = Event.objects.create(
        organizer=category_event.organizer,
        name="Other category event",
        description="Another event.",
        venue="Other Hall",
        city="Dhaka",
        event_date=date(2027, 9, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=1,
    )
    seat = category_event.seats.first()
    other_category = other_event.categories.get(name="General")
    client.force_login(category_event.organizer)

    response = client.post(
        reverse(
            "events:seat-category-assign",
            args=[category_event.pk, seat.pk],
        ),
        {"category_id": other_category.pk},
    )

    assert response.status_code == 404
    seat.refresh_from_db()
    assert seat.category.event_id == category_event.pk


@pytest.mark.django_db
def test_seat_assignment_rejects_malformed_category_id(client, category_event):
    seat = category_event.seats.first()
    client.force_login(category_event.organizer)

    response = client.post(
        reverse(
            "events:seat-category-assign",
            args=[category_event.pk, seat.pk],
        ),
        {"category_id": "not-an-id"},
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_base_strategy_calculates_prices_from_each_category(category_event):
    vip = EventCategory.objects.create(
        event=category_event,
        name="VIP",
        base_price=Decimal("250.00"),
    )

    assert calculate_seat_price(category_event, category=vip) == Decimal("250.00")
    assert calculate_seat_price(
        category_event,
        category=category_event.categories.get(name="General"),
    ) == Decimal("100.00")


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        ("start", Decimal("75.00")),
        ("within", Decimal("75.00")),
        ("end", Decimal("100.00")),
    ],
)
@pytest.mark.django_db
def test_early_bird_strategy_calculates_prices_per_category(
    category_event, at, expected
):
    starts_at = timezone.now().replace(hour=9, minute=0, second=0, microsecond=0)
    category_event.pricing_strategy = Event.PricingStrategy.EARLY_BIRD
    category_event.early_bird_price = Decimal("90.00")
    category_event.early_bird_starts_at = starts_at
    category_event.early_bird_ends_at = starts_at + timedelta(days=1)
    category_event.save()
    general = category_event.categories.get(name="General")
    general.early_bird_price = Decimal("90.00")
    general.save()
    vip = EventCategory.objects.create(
        event=category_event,
        name="VIP",
        base_price=Decimal("100.00"),
        early_bird_price=Decimal("75.00"),
    )
    times = {
        "start": category_event.early_bird_starts_at,
        "within": category_event.early_bird_starts_at + timedelta(hours=1),
        "end": category_event.early_bird_ends_at,
    }

    assert (
        calculate_seat_price(category_event, category=vip, at=times[at])
        == expected
    )
    assert calculate_seat_price(
        category_event,
        category=category_event.categories.get(name="General"),
        at=times[at],
    ) == (
        Decimal("90.00")
        if at in ("start", "within")
        else Decimal("100.00")
    )


@pytest.mark.django_db
def test_early_bird_event_requires_each_category_early_price(category_event):
    category_event.pricing_strategy = Event.PricingStrategy.EARLY_BIRD
    category_event.early_bird_price = Decimal("90.00")
    category_event.early_bird_starts_at = timezone.now()
    category_event.early_bird_ends_at = timezone.now() + timedelta(days=1)
    category_event.save()
    category = category_event.categories.get(name="General")
    category.early_bird_price = None

    with pytest.raises(ValidationError):
        category.save()


@pytest.mark.django_db
def test_customer_event_detail_displays_seat_categories_and_current_prices(
    client, category_event
):
    vip = EventCategory.objects.create(
        event=category_event,
        name="VIP",
        base_price=Decimal("250.00"),
    )
    seat = category_event.seats.first()
    seat.category = vip
    seat.save(update_fields=("category",))

    response = client.get(reverse("events:detail", args=[category_event.pk]))

    assert response.status_code == 200
    assert b"VIP" in response.content
    assert b"250.00" in response.content
    assert b"General" in response.content
    assert b"100.00" in response.content


@pytest.mark.django_db
def test_booking_snapshots_prices_for_mixed_categories_and_ignores_client_prices(
    client, category_event, customer
):
    vip = EventCategory.objects.create(
        event=category_event,
        name="VIP",
        base_price=Decimal("250.00"),
    )
    seats = list(category_event.seats.order_by("pk"))
    seats[0].category = vip
    seats[0].save(update_fields=("category",))
    holds = create_seat_holds(
        event_id=category_event.pk,
        seat_ids=[seat.pk for seat in seats],
        customer=customer,
    )
    client.force_login(customer)

    response = client.post(
        reverse("events:booking-create", args=[category_event.pk]),
        {
            "hold_ids": [hold.pk for hold in holds],
            "unit_price": "0.01",
            "total_price": "0.01",
        },
        content_type="application/json",
    )

    assert response.status_code == 201
    assert [seat["unit_price"] for seat in response.json()["seats"]] == [
        "250.00",
        "100.00",
    ]
    assert [seat["category"] for seat in response.json()["seats"]] == [
        "VIP",
        "General",
    ]
    assert response.json()["total_price"] == "350.00"

    snapshots = list(
        BookingSeat.objects.filter(booking_id=response.json()["id"]).order_by(
            "seat__pk"
        )
    )
    vip.base_price = Decimal("500.00")
    vip.save()

    booking = Booking.objects.get(pk=response.json()["id"])
    assert [snapshot.unit_price for snapshot in snapshots] == [
        Decimal("250.00"),
        Decimal("100.00"),
    ]
    assert booking.total_price == Decimal("350.00")
    assert [snapshot.category_name for snapshot in snapshots] == [
        "VIP",
        "General",
    ]


@pytest.mark.django_db
def test_early_bird_booking_snapshots_each_category_price(
    category_event, customer
):
    starts_at = timezone.now() - timedelta(hours=1)
    category_event.pricing_strategy = Event.PricingStrategy.EARLY_BIRD
    category_event.early_bird_price = Decimal("90.00")
    category_event.early_bird_starts_at = starts_at
    category_event.early_bird_ends_at = timezone.now() + timedelta(days=1)
    category_event.save()
    general = category_event.categories.get(name="General")
    general.early_bird_price = Decimal("90.00")
    general.save()
    vip = EventCategory.objects.create(
        event=category_event,
        name="VIP",
        base_price=Decimal("250.00"),
        early_bird_price=Decimal("175.00"),
    )
    seats = list(category_event.seats.order_by("pk"))
    seats[0].category = vip
    seats[0].save(update_fields=("category",))
    holds = create_seat_holds(
        event_id=category_event.pk,
        seat_ids=[seat.pk for seat in seats],
        customer=customer,
    )

    booking = create_booking_from_holds(
        event_id=category_event.pk,
        hold_ids=[hold.pk for hold in holds],
        customer=customer,
    )

    assert list(
        booking.booking_seats.order_by("seat__pk").values_list(
            "unit_price", flat=True
        )
    ) == [Decimal("175.00"), Decimal("90.00")]
