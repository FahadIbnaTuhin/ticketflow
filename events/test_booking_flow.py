from datetime import date, time, timedelta
from decimal import Decimal
import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.models import Booking, BookingSeat, Event, EventCategory, Payment, Seat, SeatHold


@pytest.fixture
def organizer(django_user_model):
    return django_user_model.objects.create_user(
        username="flow-organizer",
        email="organizer@flow.test",
        password="Password123!",
        role=User.Role.ORGANIZER,
    )


@pytest.fixture
def customer(django_user_model):
    return django_user_model.objects.create_user(
        username="flow-customer",
        email="customer@flow.test",
        password="Password123!",
        role=User.Role.CUSTOMER,
    )


@pytest.fixture
def other_customer(django_user_model):
    return django_user_model.objects.create_user(
        username="other-customer",
        email="other@flow.test",
        password="Password123!",
        role=User.Role.CUSTOMER,
    )


@pytest.fixture
def flow_event(organizer):
    today = timezone.localdate()
    event = Event.objects.create(
        organizer=organizer,
        name="Dhaka Rock Fest",
        description="Epic concert event.",
        venue="Army Stadium",
        city="Dhaka",
        event_date=today + timedelta(days=10),
        start_time=time(19, 0),
        seat_map_rows=2,
        seats_per_row=2,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("100.00"),
    )
    # create category and seats
    cat = EventCategory.objects.create(
        event=event,
        name="VIP",
        base_price=Decimal("150.00"),
    )
    seats = list(event.seats.all())
    seats[0].category = cat
    seats[0].save()
    seats[1].category = cat
    seats[1].save()
    return event


@pytest.mark.django_db
def test_customer_can_create_real_seat_hold(client, customer, flow_event):
    client.force_login(customer)
    seat = flow_event.seats.first()

    response = client.post(
        reverse("events:create-holds", args=[flow_event.pk]),
        {"seat_ids": [seat.pk]},
        content_type="application/json",
    )

    assert response.status_code == 201
    data = response.json()
    assert "holds" in data
    assert len(data["holds"]) == 1
    assert data["holds"][0]["seat_id"] == seat.pk

    # Verify real record exists in database
    hold = SeatHold.objects.get(pk=data["holds"][0]["id"])
    assert hold.seat_id == seat.pk
    assert hold.customer_id == customer.pk
    assert hold.status == SeatHold.Status.ACTIVE
    assert hold.expires_at > timezone.now()


@pytest.mark.django_db
def test_anonymous_and_organizer_cannot_create_holds(client, organizer, flow_event):
    seat = flow_event.seats.first()

    # Anonymous
    anon_resp = client.post(
        reverse("events:create-holds", args=[flow_event.pk]),
        {"seat_ids": [seat.pk]},
        content_type="application/json",
    )
    assert anon_resp.status_code in (401, 403)

    # Organizer
    client.force_login(organizer)
    org_resp = client.post(
        reverse("events:create-holds", args=[flow_event.pk]),
        {"seat_ids": [seat.pk]},
        content_type="application/json",
    )
    assert org_resp.status_code == 403


@pytest.mark.django_db
def test_hold_conflict_when_seat_already_held(client, customer, other_customer, flow_event):
    seat = flow_event.seats.first()
    now = timezone.now()

    # First customer holds the seat
    SeatHold.objects.create(
        seat=seat,
        customer=customer,
        status=SeatHold.Status.ACTIVE,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    # Second customer tries to hold same seat
    client.force_login(other_customer)
    response = client.post(
        reverse("events:create-holds", args=[flow_event.pk]),
        {"seat_ids": [seat.pk]},
        content_type="application/json",
    )

    assert response.status_code == 409


@pytest.mark.django_db
def test_create_booking_from_active_holds(client, customer, flow_event):
    client.force_login(customer)
    seat1, seat2 = list(flow_event.seats.all())[:2]
    now = timezone.now()

    hold1 = SeatHold.objects.create(
        seat=seat1,
        customer=customer,
        status=SeatHold.Status.ACTIVE,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    hold2 = SeatHold.objects.create(
        seat=seat2,
        customer=customer,
        status=SeatHold.Status.ACTIVE,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    response = client.post(
        reverse("events:booking-create", args=[flow_event.pk]),
        {"hold_ids": [hold1.pk, hold2.pk]},
        content_type="application/json",
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "held"
    assert Decimal(data["total_price"]) == Decimal("300.00")
    assert len(data["seats"]) == 2

    # Check database Booking record
    booking = Booking.objects.get(pk=data["id"])
    assert booking.customer == customer
    assert booking.event == flow_event
    assert booking.status == Booking.Status.HELD
    assert booking.booking_seats.count() == 2

    # Holds converted
    hold1.refresh_from_db()
    hold2.refresh_from_db()
    assert hold1.status == SeatHold.Status.CONVERTED
    assert hold2.status == SeatHold.Status.CONVERTED


@pytest.mark.django_db
def test_mock_payment_confirms_booking_with_idempotency_key(client, customer, flow_event):
    client.force_login(customer)
    seat = flow_event.seats.first()
    now = timezone.now()

    hold = SeatHold.objects.create(
        seat=seat,
        customer=customer,
        status=SeatHold.Status.CONVERTED,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    booking = Booking.objects.create(
        customer=customer,
        event=flow_event,
        status=Booking.Status.HELD,
        expires_at=now + timedelta(minutes=15),
    )
    BookingSeat.objects.create(
        booking=booking,
        seat=seat,
        category=seat.category,
        category_name="VIP",
        source_hold=hold,
        unit_price=Decimal("150.00"),
        occupies_seat=True,
    )

    idempotency_key = "test-idem-key-12345"
    response = client.post(
        reverse("events:booking-start-payment", args=[booking.pk]),
        {},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idempotency_key,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["payment"]["status"] == "succeeded"
    assert data["payment"]["amount"] == "150.00"

    # Verify database state
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.payments.filter(status=Payment.Status.SUCCEEDED).exists()


@pytest.mark.django_db
def test_browser_requests_receive_html_templates(client, customer, flow_event):
    client.force_login(customer)
    seat = flow_event.seats.first()
    now = timezone.now()

    hold = SeatHold.objects.create(
        seat=seat,
        customer=customer,
        status=SeatHold.Status.CONVERTED,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    booking = Booking.objects.create(
        customer=customer,
        event=flow_event,
        status=Booking.Status.CONFIRMED,
        expires_at=now + timedelta(minutes=15),
    )
    BookingSeat.objects.create(
        booking=booking,
        seat=seat,
        category=seat.category,
        category_name="VIP",
        source_hold=hold,
        unit_price=Decimal("150.00"),
        occupies_seat=True,
    )

    # 1. Booking list HTML
    list_html_resp = client.get(
        reverse("events:booking-list"),
        HTTP_ACCEPT="text/html,application/xhtml+xml",
    )
    assert list_html_resp.status_code == 200
    assert "My Bookings" in list_html_resp.content.decode()
    assert flow_event.name in list_html_resp.content.decode()
    assert f"#TF-{booking.pk}" in list_html_resp.content.decode()

    # 2. Booking detail HTML
    detail_html_resp = client.get(
        reverse("events:booking-detail", args=[booking.pk]),
        HTTP_ACCEPT="text/html,application/xhtml+xml",
    )
    assert detail_html_resp.status_code == 200
    assert f"Booking #TF-{booking.pk}" in detail_html_resp.content.decode()
    assert flow_event.name in detail_html_resp.content.decode()
    assert "$150.00" in detail_html_resp.content.decode()
    assert "Confirmed" in detail_html_resp.content.decode()

    # 3. API request still receives JSON
    detail_json_resp = client.get(
        reverse("events:booking-detail", args=[booking.pk]),
    )
    assert detail_json_resp.status_code == 200
    assert detail_json_resp.json()["id"] == booking.pk
