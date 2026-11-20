from datetime import date, time
from decimal import Decimal

import pytest
from django.core import mail
from django.urls import reverse

from .booking_services import create_booking_from_holds, transition_booking
from .models import Booking, Event, Notification, Payment, Seat
from .notification_channels import dispatch_booking_notification
from .payment_services import initiate_booking_payment
from .services import create_seat_holds


@pytest.fixture
def notification_booking(django_user_model):
    organizer = django_user_model.objects.create_user(
        username="notification-organizer",
        email="organizer@example.test",
        role=django_user_model.Role.ORGANIZER,
    )
    customer = django_user_model.objects.create_user(
        username="notification-customer",
        email="customer@example.test",
    )
    event = Event.objects.create(
        organizer=organizer,
        name="Notification test event",
        description="An event used for notification tests.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 10, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=2,
        base_price=Decimal("10.00"),
    )
    seat = Seat.objects.filter(event=event).first()
    hold = create_seat_holds(
        event_id=event.pk,
        seat_ids=[seat.pk],
        customer=customer,
    )[0]
    booking = create_booking_from_holds(
        event_id=event.pk,
        hold_ids=[hold.pk],
        customer=customer,
    )
    return customer, organizer, booking


def confirm_booking(booking, customer):
    transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.PENDING_PAYMENT,
        actor=customer,
        reason="Payment initiated.",
    )
    return transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.CONFIRMED,
        reason="Payment provider confirmed.",
    )


@pytest.mark.django_db(transaction=True)
def test_booking_confirmation_creates_in_app_and_customer_email(
    notification_booking, settings
):
    customer, organizer, booking = notification_booking
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    confirm_booking(booking, customer)

    notification = Notification.objects.get(booking=booking)
    assert notification.recipient == customer
    assert notification.notification_type == Notification.Type.BOOKING_CONFIRMED
    assert notification.is_read is False
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [customer.email]
    assert organizer.email not in mail.outbox[0].to
    assert "confirmed" in mail.outbox[0].body


@pytest.mark.django_db(transaction=True)
def test_cancellation_creates_notification_and_email(notification_booking, settings):
    customer, _, booking = notification_booking
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.CANCELLED,
        actor=customer,
        reason="Customer requested cancellation.",
    )

    notification = Notification.objects.get(booking=booking)
    assert notification.notification_type == Notification.Type.BOOKING_CANCELLED
    assert notification.recipient == customer
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [customer.email]
    assert "cancelled" in mail.outbox[0].body


@pytest.mark.django_db(transaction=True)
def test_duplicate_transition_notification_dispatch_is_idempotent(
    notification_booking, settings
):
    customer, _, booking = notification_booking
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    confirm_booking(booking, customer)
    notification = Notification.objects.get(booking=booking)

    for _ in range(2):
        dispatch_booking_notification(
            booking=booking,
            notification_type=Notification.Type.BOOKING_CONFIRMED,
            message=notification.message,
        )

    assert Notification.objects.filter(
        booking=booking,
        notification_type=Notification.Type.BOOKING_CONFIRMED,
    ).count() == 1
    assert len(mail.outbox) == 1


@pytest.mark.django_db(transaction=True)
def test_pending_and_failed_payment_do_not_send_notifications(
    notification_booking, settings
):
    customer, _, booking = notification_booking
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.PAYMENT_PROVIDER = "mock_failure"

    payment = initiate_booking_payment(
        booking_id=booking.pk,
        customer=customer,
        idempotency_key="notification-failed-payment",
    )

    booking.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert booking.status == Booking.Status.PENDING_PAYMENT
    assert not Notification.objects.filter(booking=booking).exists()
    assert mail.outbox == []


@pytest.mark.django_db(transaction=True)
def test_email_failure_does_not_undo_booking_or_in_app_notification(
    notification_booking, settings, monkeypatch, caplog
):
    customer, _, booking = notification_booking
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def fail_email(*args, **kwargs):
        raise OSError("mail backend unavailable")

    monkeypatch.setattr("events.notification_channels.send_mail", fail_email)
    confirm_booking(booking, customer)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert Notification.objects.filter(
        booking=booking,
        recipient=customer,
        notification_type=Notification.Type.BOOKING_CONFIRMED,
    ).exists()
    assert "Could not send booking notification email" in caplog.text


@pytest.mark.django_db
def test_customer_can_list_and_mark_own_notification_read(
    client, notification_booking
):
    customer, _, booking = notification_booking
    notification = Notification.objects.create(
        recipient=customer,
        booking=booking,
        notification_type=Notification.Type.BOOKING_CONFIRMED,
        message="Booking confirmed.",
    )
    client.force_login(customer)

    listed = client.get(reverse("events:notification-list"))
    marked = client.post(
        reverse("events:notification-read", args=[notification.pk])
    )

    notification.refresh_from_db()
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == notification.pk
    assert marked.status_code == 200
    assert marked.json()["is_read"] is True
    assert notification.is_read is True


@pytest.mark.django_db
def test_customer_cannot_list_or_modify_another_users_notification(
    client, notification_booking, django_user_model
):
    customer, _, booking = notification_booking
    other_customer = django_user_model.objects.create_user(
        username="notification-other-customer",
    )
    notification = Notification.objects.create(
        recipient=customer,
        booking=booking,
        notification_type=Notification.Type.BOOKING_CONFIRMED,
        message="Booking confirmed.",
    )
    client.force_login(other_customer)

    listed = client.get(reverse("events:notification-list"))
    marked = client.post(
        reverse("events:notification-read", args=[notification.pk])
    )

    notification.refresh_from_db()
    assert listed.status_code == 200
    assert listed.json() == []
    assert marked.status_code == 404
    assert notification.is_read is False


@pytest.mark.django_db
def test_organizer_cannot_use_customer_notification_endpoints(
    client, notification_booking
):
    _, organizer, _ = notification_booking
    client.force_login(organizer)

    response = client.get(reverse("events:notification-list"))

    assert response.status_code == 403
