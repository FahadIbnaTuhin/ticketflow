from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from events.models import Booking, BookingSeat, Event, EventCategory, Seat, SeatHold

User = get_user_model()


@pytest.fixture
def organizer(django_user_model):
    return django_user_model.objects.create_user(
        username="detail-organizer",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )


@pytest.fixture
def customer(django_user_model):
    return django_user_model.objects.create_user(
        username="detail-customer",
        password="StrongPassword123!",
        role=django_user_model.Role.CUSTOMER,
    )


@pytest.fixture
def event_with_seats(organizer):
    today = timezone.localdate()
    now = timezone.now()
    event = Event.objects.create(
        organizer=organizer,
        name="Grand Dhaka Symphony",
        description="An evening of live classical symphony performances.",
        venue="National Theatre Hall",
        city="Dhaka",
        event_date=today + timedelta(days=10),
        start_time=time(19, 30),
        seat_map_rows=3,
        seats_per_row=4,
        pricing_strategy=Event.PricingStrategy.EARLY_BIRD,
        base_price=Decimal("120.00"),
        early_bird_price=Decimal("90.00"),
        early_bird_starts_at=now - timedelta(days=1),
        early_bird_ends_at=now + timedelta(days=5),
    )
    # Event post_save automatically creates General category and 12 seats
    # Add a VIP category and assign Row 1 seats to it
    vip_category = EventCategory.objects.create(
        event=event,
        name="VIP Front",
        base_price=Decimal("200.00"),
        early_bird_price=Decimal("150.00"),
    )
    event.seats.filter(row_number=1).update(category=vip_category)
    return event


@pytest.mark.django_db
def test_event_details_page_loads_successfully(client, event_with_seats):
    response = client.get(reverse("events:detail", args=[event_with_seats.pk]))

    assert response.status_code == 200
    templates = [t.name for t in response.templates if t.name]
    assert "events/event_detail.html" in templates
    assert "base.html" in templates


@pytest.mark.django_db
def test_correct_event_information_displayed(client, event_with_seats):
    response = client.get(reverse("events:detail", args=[event_with_seats.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Grand Dhaka Symphony" in content
    assert "National Theatre Hall" in content
    assert "Dhaka" in content
    assert "An evening of live classical symphony performances." in content
    assert "detail-organizer" in content
    assert "12 seats capacity" in content
    assert "12 seats available" in content
    assert "Early Bird Strategy" in content


@pytest.mark.django_db
def test_seat_categories_and_pricing_rendered(client, event_with_seats):
    response = client.get(reverse("events:detail", args=[event_with_seats.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "VIP Front" in content
    assert "General" in content
    # Early bird is active, so early bird prices are displayed
    assert "$150.00" in content
    assert "$90.00" in content


@pytest.mark.django_db
def test_seat_inventory_renders_rows_and_seat_numbers(client, event_with_seats):
    response = client.get(reverse("events:detail", args=[event_with_seats.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Row 1" in content
    assert "Row 2" in content
    assert "Row 3" in content
    # Total 12 seat buttons rendered
    assert content.count('class="seat-btn ') == 12
    # All are available initially
    assert content.count('class="seat-btn seat-available"') == 12


@pytest.mark.django_db
def test_held_and_booked_seats_are_marked_unavailable(client, customer, event_with_seats):
    now = timezone.now()
    seats = list(event_with_seats.seats.order_by("row_number", "seat_number"))

    # Seat (Row 1, Seat 1) is held
    held_seat = seats[0]
    hold = SeatHold.objects.create(
        seat=held_seat,
        customer=customer,
        status=SeatHold.Status.ACTIVE,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    # Seat (Row 1, Seat 2) is booked
    booked_seat = seats[1]
    booking_hold = SeatHold.objects.create(
        seat=booked_seat,
        customer=customer,
        status=SeatHold.Status.CONVERTED,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    booking = Booking.objects.create(
        customer=customer,
        event=event_with_seats,
        status=Booking.Status.CONFIRMED,
        expires_at=now + timedelta(hours=1),
    )
    BookingSeat.objects.create(
        booking=booking,
        seat=booked_seat,
        category=booked_seat.category,
        category_name="VIP Front",
        source_hold=booking_hold,
        unit_price=Decimal("150.00"),
        occupies_seat=True,
    )

    response = client.get(reverse("events:detail", args=[event_with_seats.pk]))

    assert response.status_code == 200
    assert response.context["total_seats"] == 12
    assert response.context["available_seats"] == 10
    assert response.context["unavailable_seats"] == 2

    content = response.content.decode()
    # 2 seats marked unavailable
    assert content.count('class="seat-btn seat-unavailable"') == 2
    # 10 seats marked available
    assert content.count('class="seat-btn seat-available"') == 10
    # Unavailable seats have disabled attribute
    assert f'data-seat-id="{held_seat.pk}"' in content
    assert f'data-seat-id="{booked_seat.pk}"' in content


@pytest.mark.django_db
def test_expired_hold_is_released_as_available(client, customer, event_with_seats):
    past = timezone.now() - timedelta(minutes=10)
    seat = event_with_seats.seats.first()
    # Expired hold
    SeatHold.objects.create(
        seat=seat,
        customer=customer,
        status=SeatHold.Status.ACTIVE,
        created_at=past - timedelta(minutes=5),
        expires_at=past,
    )

    response = client.get(reverse("events:detail", args=[event_with_seats.pk]))

    assert response.status_code == 200
    # Expired hold was lazily expired and seat is available
    assert response.context["available_seats"] == 12
    assert response.context["unavailable_seats"] == 0


@pytest.mark.django_db
def test_event_without_seats_renders_empty_state_gracefully(client, organizer):
    today = timezone.localdate()
    empty_event = Event.objects.create(
        organizer=organizer,
        name="Empty Arena Event",
        description="Event with zero inventory.",
        venue="Open Field",
        city="Barishal",
        event_date=today + timedelta(days=5),
        start_time=time(18, 0),
        seat_map_rows=1,
        seats_per_row=1,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("50.00"),
    )
    # Delete automatically created seats to test empty state
    empty_event.seats.all().delete()

    response = client.get(reverse("events:detail", args=[empty_event.pk]))

    assert response.status_code == 200
    assert response.context["total_seats"] == 0
    assert response.context["available_seats"] == 0
    content = response.content.decode()
    assert "No seats are available for this event." in content


@pytest.mark.django_db
def test_event_without_categories_calculates_price_without_crashing(client, organizer):
    today = timezone.localdate()
    uncategorized_event = Event.objects.create(
        organizer=organizer,
        name="Uncategorized Concert",
        description="Event with uncategorized seats.",
        venue="Club Hall",
        city="Dhaka",
        event_date=today + timedelta(days=8),
        start_time=time(20, 0),
        seat_map_rows=2,
        seats_per_row=2,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("75.00"),
    )
    # Remove category assignment from seats
    uncategorized_event.seats.update(category=None)
    uncategorized_event.categories.all().delete()

    response = client.get(reverse("events:detail", args=[uncategorized_event.pk]))

    assert response.status_code == 200
    assert response.context["total_seats"] == 4
    assert response.context["available_seats"] == 4
    content = response.content.decode()
    assert "$75.00" in content


@pytest.mark.django_db
def test_organizer_controls_visible_only_to_owner(client, organizer, customer, event_with_seats):
    # Anonymous
    anon_resp = client.get(reverse("events:detail", args=[event_with_seats.pk]))
    assert "Edit event" not in anon_resp.content.decode()
    assert "Manage seat categories" not in anon_resp.content.decode()

    # Customer
    client.force_login(customer)
    cust_resp = client.get(reverse("events:detail", args=[event_with_seats.pk]))
    assert "Edit event" not in cust_resp.content.decode()
    assert "Manage seat categories" not in cust_resp.content.decode()

    # Organizer owner
    client.force_login(organizer)
    org_resp = client.get(reverse("events:detail", args=[event_with_seats.pk]))
    assert "Edit event" in org_resp.content.decode()
    assert "Manage seat categories" in org_resp.content.decode()
