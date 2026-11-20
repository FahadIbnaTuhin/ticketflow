from html import unescape
import re
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.urls import reverse
from django.utils import timezone

from .models import Event, Seat, SeatHold
from .services import (
    CustomerRequired,
    InvalidSeatSelection,
    SeatUnavailable,
    create_seat_holds,
)


@pytest.fixture
def organizer(django_user_model):
    return django_user_model.objects.create_user(
        username="organizer",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )


@pytest.fixture
def another_organizer(django_user_model):
    return django_user_model.objects.create_user(
        username="another-organizer",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )


@pytest.fixture
def customer(django_user_model):
    return django_user_model.objects.create_user(
        username="customer",
        password="StrongPassword123!",
    )


@pytest.fixture
def event(organizer):
    return Event.objects.create(
        organizer=organizer,
        name="Community Concert",
        description="A local live music event.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 1, 15),
        start_time=time(18, 30),
        seat_map_rows=10,
        seats_per_row=20,
    )


def event_form_data(**overrides):
    data = {
        "name": "Community Concert",
        "description": "A local live music event.",
        "venue": "Main Hall",
        "city": "Dhaka",
        "event_date": "2027-01-15",
        "start_time": "18:30",
        "seat_map_rows": "10",
        "seats_per_row": "20",
        "pricing_strategy": Event.PricingStrategy.BASE,
        "base_price": "10.00",
    }
    return {**data, **overrides}


@pytest.mark.django_db
def test_organizer_can_create_event(client, organizer):
    client.force_login(organizer)

    response = client.post(reverse("events:create"), event_form_data())

    assert response.status_code == 302
    created_event = Event.objects.get(name="Community Concert")
    assert created_event.organizer == organizer
    assert created_event.pricing_strategy == Event.PricingStrategy.BASE
    category = created_event.categories.get(name="General")
    assert category.base_price == created_event.base_price
    assert created_event.seats.filter(category=category).count() == (
        created_event.seat_map_rows * created_event.seats_per_row
    )


@pytest.mark.django_db
def test_organizer_can_configure_event_early_bird_pricing(client, organizer):
    client.force_login(organizer)
    data = event_form_data(
        pricing_strategy=Event.PricingStrategy.EARLY_BIRD,
        early_bird_price="7.50",
        early_bird_starts_at="2026-11-20 09:00",
        early_bird_ends_at="2026-12-01 09:00",
    )

    response = client.post(reverse("events:create"), data)

    assert response.status_code == 302
    created_event = Event.objects.get(name="Community Concert")
    assert created_event.pricing_strategy == Event.PricingStrategy.EARLY_BIRD
    assert str(created_event.base_price) == "10.00"
    assert str(created_event.early_bird_price) == "7.50"
    assert str(created_event.categories.get(name="General").early_bird_price) == "7.50"


@pytest.mark.django_db
def test_event_form_requires_fields(client, organizer):
    client.force_login(organizer)

    response = client.post(reverse("events:create"), {})

    assert response.status_code == 200
    assert "name" in response.context["form"].errors
    assert "event_date" in response.context["form"].errors
    assert Event.objects.count() == 0


@pytest.mark.django_db
def test_organizer_can_update_and_delete_own_event(client, organizer, event):
    client.force_login(organizer)

    update_response = client.post(
        reverse("events:update", args=[event.pk]),
        event_form_data(name="Updated concert"),
    )
    assert update_response.status_code == 302
    event.refresh_from_db()
    assert event.name == "Updated concert"
    assert event.organizer == organizer

    delete_response = client.post(reverse("events:delete", args=[event.pk]))
    assert delete_response.status_code == 302
    assert not Event.objects.filter(pk=event.pk).exists()


@pytest.mark.django_db
def test_organizer_cannot_modify_or_delete_another_organizers_event(
    client, another_organizer, event
):
    client.force_login(another_organizer)

    update_response = client.post(
        reverse("events:update", args=[event.pk]),
        event_form_data(name="Unauthorized edit"),
    )
    delete_response = client.post(reverse("events:delete", args=[event.pk]))

    assert update_response.status_code == 404
    assert delete_response.status_code == 404
    event.refresh_from_db()
    assert event.name == "Community Concert"
    assert Event.objects.filter(pk=event.pk).exists()


@pytest.mark.django_db
def test_customer_cannot_access_organizer_management(client, customer, event):
    client.force_login(customer)

    responses = [
        client.get(reverse("events:organizer-list")),
        client.get(reverse("events:create")),
        client.get(reverse("events:update", args=[event.pk])),
        client.post(reverse("events:delete", args=[event.pk])),
    ]

    assert all(response.status_code == 403 for response in responses)
    assert Event.objects.filter(pk=event.pk).exists()


@pytest.mark.django_db
def test_anonymous_visitor_cannot_access_organizer_management(client, event):
    response = client.get(reverse("events:create"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.django_db
def test_event_listing_and_detail_are_public(client, event):
    listing_response = client.get(reverse("events:list"))
    detail_response = client.get(reverse("events:detail", args=[event.pk]))

    assert listing_response.status_code == 200
    assert event in listing_response.context["events"]
    assert detail_response.status_code == 200
    assert detail_response.context["event"] == event


@pytest.mark.django_db
def test_event_search_matches_name_case_insensitively(client, event):
    response = client.get(reverse("events:list"), {"q": "COMMUNITY"})

    assert response.status_code == 200
    assert list(response.context["events"]) == [event]


@pytest.mark.django_db
def test_event_list_filters_by_city(client, organizer, event):
    other_city_event = Event.objects.create(
        organizer=organizer,
        name="Other City Concert",
        description="A concert in another city.",
        venue="Other Hall",
        city="Chittagong",
        event_date=event.event_date,
        start_time=event.start_time,
        seat_map_rows=1,
        seats_per_row=1,
    )

    response = client.get(reverse("events:list"), {"city": "dhaka"})

    assert response.status_code == 200
    assert list(response.context["events"]) == [event]
    assert other_city_event not in response.context["events"]


@pytest.mark.django_db
def test_event_list_filters_by_selected_date(client, organizer, event):
    other_date_event = Event.objects.create(
        organizer=organizer,
        name="Later Concert",
        description="A concert on a different date.",
        venue="Main Hall",
        city=event.city,
        event_date=date(2027, 1, 16),
        start_time=event.start_time,
        seat_map_rows=1,
        seats_per_row=1,
    )

    response = client.get(
        reverse("events:list"),
        {"date": event.event_date.isoformat()},
    )

    assert response.status_code == 200
    assert list(response.context["events"]) == [event]
    assert other_date_event not in response.context["events"]


@pytest.mark.django_db
def test_event_list_combines_search_city_and_date_filters(client, organizer, event):
    matching_event = Event.objects.create(
        organizer=organizer,
        name="Community Concert Matinee",
        description="A matching concert.",
        venue="Main Hall",
        city=event.city,
        event_date=event.event_date,
        start_time=time(14, 0),
        seat_map_rows=1,
        seats_per_row=1,
    )
    Event.objects.create(
        organizer=organizer,
        name="Community Concert Elsewhere",
        description="A concert in another city.",
        venue="Other Hall",
        city="Chittagong",
        event_date=event.event_date,
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=1,
    )

    response = client.get(
        reverse("events:list"),
        {
            "q": "community",
            "city": "DHAKA",
            "date": event.event_date.isoformat(),
        },
    )

    assert response.status_code == 200
    assert list(response.context["events"]) == [matching_event, event]


@pytest.mark.django_db
def test_event_list_paginates_ten_and_preserves_filters(client, organizer, event):
    for index in range(10):
        Event.objects.create(
            organizer=organizer,
            name=f"Community Concert {index}",
            description="A community concert.",
            venue="Main Hall",
            city=event.city,
            event_date=event.event_date,
            start_time=time(18, index),
            seat_map_rows=1,
            seats_per_row=1,
        )

    filters = {
        "q": "community",
        "city": event.city,
        "date": event.event_date.isoformat(),
    }
    response = client.get(reverse("events:list"), {**filters, "page": "1"})

    assert response.status_code == 200
    assert len(response.context["events"]) == 10
    assert response.context["page_obj"].paginator.count == 11
    next_link = re.search(
        r'<a href="([^"]+)">\s*Next',
        response.content.decode(),
    )
    assert next_link is not None
    next_href = next_link.group(1)
    query = parse_qs(urlparse(unescape(next_href)).query)
    assert query == {
        "q": ["community"],
        "city": [event.city],
        "date": [event.event_date.isoformat()],
        "page": ["2"],
    }


@pytest.mark.django_db
@pytest.mark.parametrize("page", ["not-a-number", "999"])
def test_event_list_handles_invalid_and_out_of_range_pages(client, event, page):
    response = client.get(reverse("events:list"), {"page": page})

    assert response.status_code == 200
    assert response.context["page_obj"].number == (
        1 if page == "not-a-number" else response.context["page_obj"].paginator.num_pages
    )
    assert event in response.context["events"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("rows", "seats_per_row"),
    [(0, 10), (10, 0), (201, 10), (10, 201), (101, 100)],
)
def test_invalid_seat_map_dimensions_fail_model_validation(
    organizer, rows, seats_per_row
):
    invalid_event = Event(
        organizer=organizer,
        name="Invalid capacity",
        description="Invalid seat map.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 1, 15),
        start_time=time(18, 30),
        seat_map_rows=rows,
        seats_per_row=seats_per_row,
    )

    with pytest.raises(ValidationError):
        invalid_event.full_clean()


@pytest.mark.django_db
def test_invalid_seat_map_dimensions_are_rejected_on_creation(client, organizer):
    client.force_login(organizer)

    response = client.post(
        reverse("events:create"),
        event_form_data(seat_map_rows="101", seats_per_row="100"),
    )

    assert response.status_code == 200
    assert "seats_per_row" in response.context["form"].errors
    assert Event.objects.count() == 0


@pytest.mark.django_db
def test_event_creation_generates_every_seat(event):
    seats = Seat.objects.filter(event=event)

    assert seats.count() == event.seat_map_rows * event.seats_per_row
    assert seats.filter(row_number=1, seat_number=1).exists()
    assert seats.filter(
        row_number=event.seat_map_rows,
        seat_number=event.seats_per_row,
    ).exists()


@pytest.mark.django_db
def test_seat_coordinates_are_unique_for_an_event(event):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Seat.objects.create(event=event, row_number=1, seat_number=1)


@pytest.mark.django_db
def test_hold_has_exact_five_minute_expiry(client, customer, event, monkeypatch):
    now = timezone.now()
    monkeypatch.setattr("events.services.timezone.now", lambda: now)
    seat = Seat.objects.filter(event=event).first()

    holds = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )

    assert len(holds) == 1
    assert holds[0].created_at == now
    assert holds[0].expires_at == now + timedelta(minutes=5)
    assert holds[0].status == SeatHold.Status.ACTIVE


@pytest.mark.django_db
def test_another_customer_cannot_hold_an_active_seat(
    customer, django_user_model, event
):
    other_customer = django_user_model.objects.create_user(
        username="other-customer",
        password="StrongPassword123!",
    )
    seat = Seat.objects.filter(event=event).first()
    create_seat_holds(event_id=event.pk, seat_ids=[seat.pk], customer=customer)

    with pytest.raises(SeatUnavailable):
        create_seat_holds(
            event_id=event.pk,
            seat_ids=[seat.pk],
            customer=other_customer,
        )

    assert SeatHold.objects.filter(seat=seat, status=SeatHold.Status.ACTIVE).count() == 1


@pytest.mark.django_db
def test_expired_hold_is_released_for_another_customer(
    customer, django_user_model, event, monkeypatch
):
    other_customer = django_user_model.objects.create_user(
        username="other-customer",
        password="StrongPassword123!",
    )
    start = timezone.now()
    monkeypatch.setattr("events.services.timezone.now", lambda: start)
    seat = Seat.objects.filter(event=event).first()
    original_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]
    monkeypatch.setattr(
        "events.services.timezone.now",
        lambda: original_hold.expires_at,
    )

    replacement_holds = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=other_customer,
    )

    original_hold.refresh_from_db()
    assert original_hold.status == SeatHold.Status.EXPIRED
    assert len(replacement_holds) == 1
    assert replacement_holds[0].customer == other_customer


@pytest.mark.django_db
def test_same_customer_repeat_returns_hold_without_extending_expiry(
    customer, event, monkeypatch
):
    now = timezone.now()
    monkeypatch.setattr("events.services.timezone.now", lambda: now)
    seat = Seat.objects.filter(event=event).first()
    first_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]

    repeated_hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]

    assert repeated_hold.pk == first_hold.pk
    assert repeated_hold.expires_at == first_hold.expires_at
    assert SeatHold.objects.filter(seat=seat).count() == 1


@pytest.mark.django_db
def test_hold_rejects_unknown_seats_and_seats_from_another_event(
    customer, organizer, event
):
    other_event = Event.objects.create(
        organizer=organizer,
        name="Other event",
        description="Another event.",
        venue="Second Hall",
        city="Dhaka",
        event_date=date(2027, 2, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=1,
    )
    other_event_seat = Seat.objects.get(event=other_event)

    with pytest.raises(InvalidSeatSelection):
        create_seat_holds(event_id=event.pk, seat_ids=[999999], customer=customer)
    with pytest.raises(InvalidSeatSelection):
        create_seat_holds(
            event_id=event.pk,
            seat_ids=[other_event_seat.pk],
            customer=customer,
        )


@pytest.mark.django_db
def test_multi_seat_hold_is_atomic_when_one_seat_is_unavailable(
    customer, django_user_model, event
):
    other_customer = django_user_model.objects.create_user(
        username="other-customer",
        password="StrongPassword123!",
    )
    seats = list(Seat.objects.filter(event=event).order_by("pk")[:2])
    create_seat_holds(
        event_id=event.pk,
        seat_ids=[seats[1].pk],
        customer=other_customer,
    )

    with pytest.raises(SeatUnavailable):
        create_seat_holds(
            event_id=event.pk,
            seat_ids=[seats[0].pk, seats[1].pk],
            customer=customer,
        )

    assert not SeatHold.objects.filter(
        seat=seats[0],
        status=SeatHold.Status.ACTIVE,
    ).exists()
    assert SeatHold.objects.filter(
        seat=seats[1],
        customer=other_customer,
        status=SeatHold.Status.ACTIVE,
    ).exists()


@pytest.mark.django_db
def test_organizers_cannot_create_holds(organizer, event):
    seat = Seat.objects.filter(event=event).first()

    with pytest.raises(CustomerRequired):
        create_seat_holds(
            event_id=event.pk,
            seat_ids=[seat.pk],
            customer=organizer,
        )


@pytest.mark.django_db
def test_event_seat_map_cannot_be_resized_after_inventory_exists(
    client, organizer, event
):
    client.force_login(organizer)
    response = client.post(
        reverse("events:update", args=[event.pk]),
        event_form_data(seat_map_rows="9"),
    )

    assert response.status_code == 200
    assert "seat_map_rows" in response.context["form"].errors
    event.refresh_from_db()
    assert event.seat_map_rows == 10
    assert Seat.objects.filter(event=event).count() == 200


@pytest.mark.django_db
def test_event_deletion_cascades_seats_and_holds(customer, event):
    seat = Seat.objects.filter(event=event).first()
    hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]

    event.delete()

    assert not Seat.objects.filter(pk=seat.pk).exists()
    assert not SeatHold.objects.filter(pk=hold.pk).exists()


@pytest.mark.django_db
def test_hold_endpoint_returns_expiry_for_authenticated_customer(
    client, customer, event
):
    client.force_login(customer)
    seat = Seat.objects.filter(event=event).first()

    response = client.post(
        reverse("events:create-holds", args=[event.pk]),
        {"seat_ids": [seat.pk]},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["holds"][0]["seat_id"] == seat.pk
    assert response.json()["holds"][0]["expires_at"]


@pytest.mark.django_db
def test_hold_endpoint_rejects_organizer(client, organizer, event):
    client.force_login(organizer)
    seat = Seat.objects.filter(event=event).first()

    response = client.post(
        reverse("events:create-holds", args=[event.pk]),
        {"seat_ids": [seat.pk]},
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Row-level concurrency behavior must be tested on PostgreSQL.",
)
@pytest.mark.django_db(transaction=True)
def test_concurrent_customers_cannot_hold_the_same_seat(
    django_user_model, organizer
):
    customers = [
        django_user_model.objects.create_user(
            username=f"concurrent-customer-{index}",
            password="StrongPassword123!",
        )
        for index in range(2)
    ]
    event = Event.objects.create(
        organizer=organizer,
        name="Concurrent event",
        description="A concurrency test event.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 3, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=1,
    )
    seat_id = Seat.objects.get(event=event).pk
    barrier = Barrier(2)

    def attempt_hold(customer_id):
        connections.close_all()
        try:
            customer = get_user_model().objects.get(pk=customer_id)
            barrier.wait(timeout=10)
            try:
                create_seat_holds(
                    event_id=event.pk,
                    seat_ids=[seat_id],
                    customer=customer,
                )
                return "held"
            except SeatUnavailable:
                return "unavailable"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt_hold, [customer.pk for customer in customers]))

    assert sorted(results) == ["held", "unavailable"]
    assert SeatHold.objects.filter(
        seat_id=seat_id,
        status=SeatHold.Status.ACTIVE,
    ).count() == 1
