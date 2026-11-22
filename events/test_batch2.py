from datetime import date, datetime, time, timedelta
from decimal import Decimal
import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.models import (
    Booking,
    BookingSeat,
    Event,
    EventCategory,
    Notification,
    Payment,
    Refund,
    Seat,
    SeatHold,
)


@pytest.fixture
def organizer_a(django_user_model):
    return django_user_model.objects.create_user(
        username="org-a",
        email="orga@test.com",
        password="Password123!",
        role=User.Role.ORGANIZER,
    )


@pytest.fixture
def organizer_b(django_user_model):
    return django_user_model.objects.create_user(
        username="org-b",
        email="orgb@test.com",
        password="Password123!",
        role=User.Role.ORGANIZER,
    )


@pytest.fixture
def customer_user(django_user_model):
    return django_user_model.objects.create_user(
        username="cust-user",
        email="cust@test.com",
        password="Password123!",
        role=User.Role.CUSTOMER,
    )


@pytest.fixture
def event_org_a(organizer_a):
    today = timezone.localdate()
    return Event.objects.create(
        organizer=organizer_a,
        name="Org A Gala Night",
        description="Exclusive gala organized by Org A.",
        venue="Grand Ballroom",
        city="Dhaka",
        event_date=today + timedelta(days=5),
        start_time=time(18, 0),
        seat_map_rows=2,
        seats_per_row=2,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("120.00"),
    )


# ----------------------------------------------------
# 1. Customer Notifications Tests
# ----------------------------------------------------
@pytest.mark.django_db
def test_customer_notification_center_renders_html_with_updates(client, customer_user, event_org_a):
    client.force_login(customer_user)

    booking = Booking.objects.create(
        customer=customer_user,
        event=event_org_a,
        status=Booking.Status.CONFIRMED,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    notif_conf = Notification.objects.create(
        recipient=customer_user,
        booking=booking,
        notification_type=Notification.Type.BOOKING_CONFIRMED,
        message=f"Booking confirmed for {event_org_a.name}.",
    )
    notif_canc = Notification.objects.create(
        recipient=customer_user,
        booking=booking,
        notification_type=Notification.Type.BOOKING_CANCELLED,
        message=f"Booking cancelled for {event_org_a.name}.",
    )

    response = client.get(
        reverse("events:notification-list"),
        HTTP_ACCEPT="text/html,application/xhtml+xml",
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "Notification Center" in content
    assert "Booking Confirmed" in content
    assert "Booking Cancelled" in content
    assert f"#TF-{booking.pk}" in content

    # Mark as read
    read_resp = client.post(
        reverse("events:notification-read", args=[notif_conf.pk]),
        HTTP_ACCEPT="text/html,application/xhtml+xml",
    )
    assert read_resp.status_code == 302
    notif_conf.refresh_from_db()
    assert notif_conf.is_read is True


@pytest.mark.django_db
def test_organizer_cannot_access_customer_notifications(client, organizer_a):
    client.force_login(organizer_a)
    response = client.get(
        reverse("events:notification-list"),
        HTTP_ACCEPT="text/html,application/xhtml+xml",
    )
    assert response.status_code == 403


# ----------------------------------------------------
# 2. Cancellation & 24-Hour Refund Rule Tests
# ----------------------------------------------------
@pytest.mark.django_db
def test_cancellation_more_than_24_hours_issues_full_refund(client, customer_user, event_org_a):
    # Event is in 5 days (> 24 hours)
    client.force_login(customer_user)
    seat = event_org_a.seats.first()
    hold = SeatHold.objects.create(
        seat=seat,
        customer=customer_user,
        status=SeatHold.Status.CONVERTED,
        expires_at=timezone.now() + timedelta(days=1),
    )
    booking = Booking.objects.create(
        customer=customer_user,
        event=event_org_a,
        status=Booking.Status.CONFIRMED,
        expires_at=timezone.now() + timedelta(days=1),
    )
    BookingSeat.objects.create(
        booking=booking,
        seat=seat,
        category=seat.category,
        category_name="General",
        source_hold=hold,
        unit_price=Decimal("120.00"),
        occupies_seat=True,
    )
    payment = Payment.objects.create(
        booking=booking,
        provider="mock_success",
        provider_reference="MOCK-REF-999",
        amount=Decimal("120.00"),
        status=Payment.Status.SUCCEEDED,
        idempotency_key="pay-key-999",
    )

    response = client.post(
        reverse("events:booking-cancel", args=[booking.pk]),
        {"reason": "Changed my schedule"},
        HTTP_ACCEPT="text/html,application/xhtml+xml",
    )
    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED

    # Seat freed
    assert BookingSeat.objects.get(booking=booking).occupies_seat is False

    # Refund created and succeeded
    refund = Refund.objects.get(payment=payment)
    assert refund.status == Refund.Status.SUCCEEDED
    assert refund.amount == Decimal("120.00")

    # Detail page reflects cancellation & refund
    detail_resp = client.get(
        reverse("events:booking-detail", args=[booking.pk]),
        HTTP_ACCEPT="text/html,application/xhtml+xml",
    )
    assert detail_resp.status_code == 200
    assert "Cancelled" in detail_resp.content.decode()
    assert "Refund Information" in detail_resp.content.decode()
    assert "$120.00" in detail_resp.content.decode()


@pytest.mark.django_db
def test_cancellation_within_24_hours_receives_no_refund(client, customer_user, organizer_a):
    # Event starting in 2 hours
    today = timezone.localdate()
    soon_event = Event.objects.create(
        organizer=organizer_a,
        name="Last Minute Concert",
        description="Starts very soon.",
        venue="City Hall",
        city="Dhaka",
        event_date=today,
        start_time=time(23, 59),
        seat_map_rows=1,
        seats_per_row=1,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("80.00"),
    )
    client.force_login(customer_user)
    seat = soon_event.seats.first()
    hold = SeatHold.objects.create(
        seat=seat,
        customer=customer_user,
        status=SeatHold.Status.CONVERTED,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    booking = Booking.objects.create(
        customer=customer_user,
        event=soon_event,
        status=Booking.Status.CONFIRMED,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    BookingSeat.objects.create(
        booking=booking,
        seat=seat,
        category=seat.category,
        category_name="General",
        source_hold=hold,
        unit_price=Decimal("80.00"),
        occupies_seat=True,
    )
    payment = Payment.objects.create(
        booking=booking,
        provider="mock_success",
        provider_reference="MOCK-REF-888",
        amount=Decimal("80.00"),
        status=Payment.Status.SUCCEEDED,
        idempotency_key="pay-key-888",
    )

    response = client.post(
        reverse("events:booking-cancel", args=[booking.pk]),
        {"reason": "Emergency"},
        HTTP_ACCEPT="text/html,application/xhtml+xml",
    )
    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    # No refund created
    assert not Refund.objects.filter(payment=payment).exists()


@pytest.mark.django_db
def test_repeated_cancellation_is_rejected(client, customer_user, event_org_a):
    client.force_login(customer_user)
    seat = event_org_a.seats.first()
    hold = SeatHold.objects.create(
        seat=seat,
        customer=customer_user,
        status=SeatHold.Status.CONVERTED,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    booking = Booking.objects.create(
        customer=customer_user,
        event=event_org_a,
        status=Booking.Status.CANCELLED,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    BookingSeat.objects.create(
        booking=booking,
        seat=seat,
        category=seat.category,
        category_name="General",
        source_hold=hold,
        unit_price=Decimal("120.00"),
        occupies_seat=False,
    )

    # API request returns 409
    response = client.post(
        reverse("events:booking-cancel", args=[booking.pk]),
        {"reason": "Cancel again"},
    )
    assert response.status_code == 409


# ----------------------------------------------------
# 3. Organizer Ownership & Isolation Tests
# ----------------------------------------------------
@pytest.mark.django_db
def test_organizer_can_manage_own_event_ui(client, organizer_a, event_org_a):
    client.force_login(organizer_a)

    # List events
    list_resp = client.get(reverse("events:organizer-list"))
    assert list_resp.status_code == 200
    assert event_org_a.name in list_resp.content.decode()

    # Edit event form GET
    edit_resp = client.get(reverse("events:update", args=[event_org_a.pk]))
    assert edit_resp.status_code == 200
    assert "Edit event" in edit_resp.content.decode()

    # Category list GET
    cat_resp = client.get(reverse("events:category-list", args=[event_org_a.pk]))
    assert cat_resp.status_code == 200
    assert "Seat Categories" in cat_resp.content.decode()


@pytest.mark.django_db
def test_organizer_cannot_manage_other_organizers_event(client, organizer_b, event_org_a):
    # Organizer B tries to access Organizer A's event
    client.force_login(organizer_b)

    # Edit should return 404
    edit_resp = client.get(reverse("events:update", args=[event_org_a.pk]))
    assert edit_resp.status_code == 404

    # Categories should return 404
    cat_resp = client.get(reverse("events:category-list", args=[event_org_a.pk]))
    assert cat_resp.status_code == 404

    # Delete should return 404
    del_resp = client.post(reverse("events:delete", args=[event_org_a.pk]))
    assert del_resp.status_code == 404


@pytest.mark.django_db
def test_customer_cannot_access_organizer_views(client, customer_user, event_org_a):
    client.force_login(customer_user)

    # Organizer list forbidden
    resp = client.get(reverse("events:organizer-list"))
    assert resp.status_code == 403

    # Create event forbidden
    resp = client.get(reverse("events:create"))
    assert resp.status_code == 403
