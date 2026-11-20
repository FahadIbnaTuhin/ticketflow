from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from .booking_services import create_booking_from_holds, transition_booking
from .models import Booking, Event, Payment, Refund, Seat
from .payment_providers import MockSuccessProvider
from .services import create_seat_holds


@pytest.fixture
def refundable_booking(django_user_model, monkeypatch):
    now = timezone.now().replace(microsecond=0)
    monkeypatch.setattr("events.refund_services.timezone.now", lambda: now)
    organizer = django_user_model.objects.create_user(
        username="refund-organizer",
        role=django_user_model.Role.ORGANIZER,
    )
    customer = django_user_model.objects.create_user(
        username="refund-customer",
    )
    event = Event.objects.create(
        organizer=organizer,
        name="Refund test event",
        description="An event used for refund tests.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 11, 20),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=1,
        base_price=Decimal("25.00"),
    )
    seat = Seat.objects.get(event=event)
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
    transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.PENDING_PAYMENT,
        actor=customer,
        reason="Payment initiated.",
    )
    transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.CONFIRMED,
        reason="Payment provider confirmed.",
    )
    payment = Payment.objects.create(
        booking=booking,
        provider="mock_success",
        provider_reference=f"refund-test-payment-{booking.pk}",
        idempotency_key=f"refund-test-{booking.pk}",
        amount=Decimal("25.00"),
        status=Payment.Status.SUCCEEDED,
        completed_at=now,
    )
    return customer, event, booking, payment, now


def set_event_start(event, now, hours_before):
    target_start = now + timedelta(hours=hours_before)
    local_start = timezone.localtime(target_start)
    Event.objects.filter(pk=event.pk).update(
        event_date=local_start.date(),
        start_time=local_start.time().replace(tzinfo=None),
    )


def cancel_request(client, booking):
    return client.post(
        reverse("events:booking-cancel", args=[booking.pk]),
        {"reason": "Customer cancellation."},
    )


@pytest.mark.django_db
def test_confirmed_booking_cancelled_more_than_24_hours_before_gets_full_refund(
    client, refundable_booking
):
    customer, event, booking, payment, now = refundable_booking
    set_event_start(event, now, 24 + 1 / 60)
    client.force_login(customer)

    response = cancel_request(client, booking)

    refund = Refund.objects.get(payment=payment)
    assert response.status_code == 200
    assert response.json()["status"] == Booking.Status.CANCELLED
    assert response.json()["refund"] == {"issued": True, "amount": "25.00"}
    assert refund.status == Refund.Status.SUCCEEDED
    assert refund.amount == payment.amount
    assert refund.provider_reference
    assert refund.completed_at is not None
    assert booking.status_history.filter(
        to_status=Booking.Status.CANCELLED
    ).exists()


@pytest.mark.django_db
def test_refund_cutoff_uses_active_project_timezone(
    client, refundable_booking
):
    customer, event, booking, payment, now = refundable_booking
    with timezone.override("Asia/Dhaka"):
        set_event_start(event, now, 48)
        client.force_login(customer)

        response = cancel_request(client, booking)

    assert response.status_code == 200
    assert response.json()["refund"] == {"issued": True, "amount": "25.00"}
    assert Refund.objects.get(payment=payment).status == Refund.Status.SUCCEEDED


@pytest.mark.parametrize("hours_before", [24, 23])
@pytest.mark.django_db
def test_confirmed_booking_cancelled_within_24_hours_gets_no_refund(
    client, refundable_booking, hours_before
):
    customer, event, booking, _, now = refundable_booking
    set_event_start(event, now, hours_before)
    client.force_login(customer)

    response = cancel_request(client, booking)

    assert response.status_code == 200
    assert response.json()["refund"] == {"issued": False, "amount": "0.00"}
    assert not Refund.objects.filter(payment__booking=booking).exists()
    assert booking.status_history.filter(
        to_status=Booking.Status.CANCELLED
    ).exists()


@pytest.mark.django_db
def test_duplicate_cancellation_does_not_create_second_refund(
    client, refundable_booking, monkeypatch
):
    customer, event, booking, payment, now = refundable_booking
    set_event_start(event, now, 48)
    client.force_login(customer)
    calls = []
    original_refund = MockSuccessProvider.process_refund

    def count_refunds(self, *, amount, idempotency_key):
        calls.append(idempotency_key)
        return original_refund(
            self,
            amount=amount,
            idempotency_key=idempotency_key,
        )

    monkeypatch.setattr(MockSuccessProvider, "process_refund", count_refunds)

    first = cancel_request(client, booking)
    second = cancel_request(client, booking)

    assert first.status_code == 200
    assert first.json()["refund"]["issued"] is True
    assert second.status_code == 409
    assert Refund.objects.filter(payment=payment).count() == 1
    assert len(calls) == 1


@pytest.mark.django_db
def test_another_customer_cannot_cancel_or_refund_booking(
    client, refundable_booking, django_user_model
):
    _, event, booking, payment, now = refundable_booking
    set_event_start(event, now, 48)
    other_customer = django_user_model.objects.create_user(
        username="refund-other-customer",
    )
    client.force_login(other_customer)

    response = cancel_request(client, booking)

    booking.refresh_from_db()
    assert response.status_code == 404
    assert booking.status == Booking.Status.CONFIRMED
    assert not Refund.objects.filter(payment=payment).exists()


@pytest.mark.django_db
def test_cancelling_unconfirmed_booking_does_not_refund(
    client, refundable_booking
):
    customer, event, booking, payment, now = refundable_booking
    set_event_start(event, now, 48)
    Payment.objects.filter(pk=payment.pk).update(status=Payment.Status.FAILED)
    Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.PENDING_PAYMENT)
    client.force_login(customer)

    response = cancel_request(client, booking)

    assert response.status_code == 200
    assert response.json()["refund"] == {"issued": False, "amount": "0.00"}
    assert not Refund.objects.filter(payment=payment).exists()
