from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .booking_services import create_booking_from_holds, transition_booking
from .models import Booking, Event, Payment, Seat
from .payment_providers import (
    PaymentProviderUnavailable,
    PaymentResult,
    get_payment_provider,
)
from .services import create_seat_holds
from .services import create_seat_holds


@pytest.fixture
def payment_booking(django_user_model):
    organizer = django_user_model.objects.create_user(
        username="payment-organizer",
        role=django_user_model.Role.ORGANIZER,
    )
    customer = django_user_model.objects.create_user(
        username="payment-customer",
    )
    event = Event.objects.create(
        organizer=organizer,
        name="Payment test event",
        description="An event used for payment tests.",
        venue="Main Hall",
        city="Dhaka",
        event_date=date(2027, 8, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=2,
        base_price=Decimal("12.34"),
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
    return customer, event, booking


def payment_request(client, booking, idempotency_key, *, amount=""):
    return client.post(
        reverse("events:booking-start-payment", args=[booking.pk]),
        {"amount": amount},
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idempotency_key,
    )


@pytest.mark.parametrize(
    ("provider_name", "approved"),
    [("mock_success", True), ("mock_failure", False)],
)
def test_mock_providers_simulate_configured_outcomes(
    settings, provider_name, approved
):
    settings.PAYMENT_PROVIDER = provider_name
    result = get_payment_provider().process_payment(
        amount=Decimal("12.34"),
        idempotency_key="provider-outcome-test",
    )

    assert result.approved is approved
    assert result.provider_reference


@pytest.mark.parametrize("provider_name", ["mock_success", "mock_failure"])
def test_mock_providers_are_rejected_in_production(provider_name):
    with override_settings(
        DJANGO_ENV="production",
        PAYMENT_PROVIDER=provider_name,
    ):
        with pytest.raises(
            PaymentProviderUnavailable,
            match="disabled in production",
        ):
            get_payment_provider()


def test_mock_providers_remain_available_outside_production():
    with override_settings(
        DEBUG=False,
        DJANGO_ENV="development",
        PAYMENT_PROVIDER="mock_success",
    ):
        assert get_payment_provider().name == "mock_success"


@pytest.mark.django_db
def test_production_rejects_mock_payment_without_changing_booking(
    client, payment_booking
):
    customer, _, booking = payment_booking
    client.force_login(customer)

    with override_settings(
        DJANGO_ENV="production",
        PAYMENT_PROVIDER="mock_success",
    ):
        response = payment_request(client, booking, "production-mock-request")

    assert response.status_code == 503
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD
    assert not Payment.objects.filter(booking=booking).exists()


@pytest.mark.django_db
def test_successful_payment_confirms_booking_and_uses_snapshot_amount(
    client, payment_booking, settings
):
    customer, event, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_success"

    response = payment_request(
        client,
        booking,
        "success-request-1",
        amount="0.01",
    )
    retry = payment_request(
        client,
        booking,
        "success-request-1",
        amount="999999.99",
    )

    booking.refresh_from_db()
    payment = Payment.objects.get(booking=booking)
    assert response.status_code == 200
    assert retry.status_code == 200
    assert response.json()["payment"]["id"] == retry.json()["payment"]["id"]
    assert response.json()["status"] == retry.json()["status"] == Booking.Status.CONFIRMED
    assert response.json()["payment"]["status"] == Payment.Status.SUCCEEDED
    assert response.json()["payment"]["amount"] == "12.34"
    assert payment.provider == "mock_success"
    assert payment.amount == Decimal("12.34")
    assert payment.provider_reference
    assert booking.status == Booking.Status.CONFIRMED
    assert list(
        booking.status_history.values_list("to_status", flat=True)
    ) == [Booking.Status.HELD, Booking.Status.PENDING_PAYMENT, Booking.Status.CONFIRMED]
    assert Payment.objects.filter(booking=booking).count() == 1


@pytest.mark.django_db
def test_failed_payment_keeps_booking_pending_and_same_key_is_idempotent(
    client, payment_booking, settings
):
    customer, _, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_failure"

    first = payment_request(client, booking, "declined-request-1")
    second = payment_request(client, booking, "declined-request-1")

    booking.refresh_from_db()
    assert first.status_code == second.status_code == 402
    assert first.json()["payment"]["id"] == second.json()["payment"]["id"]
    assert first.json()["payment"]["status"] == Payment.Status.FAILED
    assert booking.status == Booking.Status.PENDING_PAYMENT
    assert Payment.objects.filter(booking=booking).count() == 1
    assert list(
        booking.status_history.values_list("to_status", flat=True)
    ) == [Booking.Status.HELD, Booking.Status.PENDING_PAYMENT]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("provider_name", "expected_status", "payment_status", "booking_status"),
    [
        (
            "mock_success",
            200,
            Payment.Status.SUCCEEDED,
            Booking.Status.CONFIRMED,
        ),
        (
            "mock_failure",
            402,
            Payment.Status.FAILED,
            Booking.Status.PENDING_PAYMENT,
        ),
    ],
)
def test_environment_provider_selection_controls_payment_outcome(
    client,
    payment_booking,
    settings,
    provider_name,
    expected_status,
    payment_status,
    booking_status,
):
    customer, _, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = provider_name

    response = payment_request(client, booking, f"switch-{provider_name}")

    booking.refresh_from_db()
    payment = Payment.objects.get(booking=booking)
    assert response.status_code == expected_status
    assert payment.provider == provider_name
    assert payment.status == payment_status
    assert booking.status == booking_status


@pytest.mark.django_db
def test_booking_owner_is_required_to_initiate_payment(
    client, payment_booking, django_user_model
):
    _, _, booking = payment_booking
    other_customer = django_user_model.objects.create_user(
        username="payment-other-customer",
    )
    client.force_login(other_customer)

    response = payment_request(client, booking, "not-owner-request")

    assert response.status_code == 404
    assert not Payment.objects.filter(booking=booking).exists()
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD


@pytest.mark.django_db
def test_confirmed_booking_rejects_new_payment_attempt(
    client, payment_booking, settings
):
    customer, _, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_success"
    successful = payment_request(client, booking, "confirmed-once")
    rejected = payment_request(client, booking, "confirmed-again")

    assert successful.status_code == 200
    assert rejected.status_code == 409
    assert Payment.objects.filter(booking=booking).count() == 1
    assert booking.status_history.filter(
        to_status=Booking.Status.CONFIRMED
    ).count() == 1


@pytest.mark.django_db
def test_payment_requires_idempotency_key_without_transitioning_booking(
    client, payment_booking
):
    customer, _, booking = payment_booking
    client.force_login(customer)

    response = client.post(
        reverse("events:booking-start-payment", args=[booking.pk]),
        {},
        content_type="application/json",
    )

    assert response.status_code == 400
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD
    assert not Payment.objects.filter(booking=booking).exists()


@pytest.mark.django_db
def test_processing_payment_is_not_processed_twice_by_immediate_retry(
    client, payment_booking, settings, monkeypatch
):
    customer, _, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_success"
    calls = []

    def approved_once(self, *, amount, idempotency_key):
        calls.append(idempotency_key)
        return PaymentResult(
            approved=True,
            provider_reference="processing-retry-reference",
            message="Mock payment approved.",
        )

    monkeypatch.setattr(
        "events.payment_providers.MockSuccessProvider.process_payment",
        approved_once,
    )

    first = payment_request(client, booking, "same-processing-key")
    second = payment_request(client, booking, "same-processing-key")

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1
    assert first.json()["payment"]["id"] == second.json()["payment"]["id"]


@pytest.mark.django_db
def test_existing_processing_payment_blocks_immediate_different_key(
    client, payment_booking
):
    customer, _, booking = payment_booking
    transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.PENDING_PAYMENT,
        actor=customer,
        reason="Payment initiated.",
    )
    existing = Payment.objects.create(
        booking=booking,
        provider="mock_success",
        idempotency_key="processing-key",
        amount=booking.total_price,
        processing_started_at=timezone.now(),
    )
    client.force_login(customer)

    same_key = payment_request(client, booking, "processing-key")
    different_key = payment_request(client, booking, "different-key")

    assert same_key.status_code == 202
    assert same_key.json()["payment"]["id"] == existing.pk
    assert different_key.status_code == 409
    assert Payment.objects.filter(booking=booking).count() == 1

@pytest.mark.django_db
def test_unexpected_provider_exception_marks_payment_failed(
    client, payment_booking, settings, monkeypatch
):
    customer, _, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_success"

    def broken_provider(self, *, amount, idempotency_key):
        raise RuntimeError("provider implementation leaked an error")

    monkeypatch.setattr(
        "events.payment_providers.MockSuccessProvider.process_payment",
        broken_provider,
    )

    response = payment_request(client, booking, "broken-provider")

    assert response.status_code == 503
    booking.refresh_from_db()
    payment = Payment.objects.get(booking=booking)
    assert payment.status == Payment.Status.FAILED
    assert payment.completed_at is not None
    assert "unexpected" in payment.failure_reason.lower()
    assert booking.status == Booking.Status.PENDING_PAYMENT


@pytest.mark.django_db
def test_payment_amount_provider_and_idempotency_key_are_immutable(
    django_user_model, payment_booking
):
    _, _, booking = payment_booking
    customer = django_user_model.objects.create_user(
        username="immutable-payment-customer",
    )
    payment = Payment.objects.create(
        booking=booking,
        provider="mock_success",
        idempotency_key="immutable-payment",
        amount=Decimal("12.34"),
    )

    payment.amount = Decimal("0.01")
    with pytest.raises(Exception, match="immutable"):
        payment.save()

@pytest.mark.django_db
def test_unavailable_provider_does_not_change_booking_or_create_payment(
    client, payment_booking, settings
):
    customer, _, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "not-configured"

    response = payment_request(client, booking, "unavailable-provider")

    assert response.status_code == 503
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD
    assert not Payment.objects.filter(booking=booking).exists()


@pytest.mark.django_db
def test_booking_expiring_during_provider_call_is_not_confirmed(
    client, payment_booking, settings, monkeypatch
):
    customer, _, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_success"

    def approve_after_expiry(self, *, amount, idempotency_key):
        now = timezone.now()
        Booking.objects.filter(pk=booking.pk).update(
            created_at=now - timedelta(minutes=10),
            expires_at=now - timedelta(seconds=1),
        )
        return PaymentResult(
            approved=True,
            provider_reference="expired-payment-reference",
            message="Mock payment approved.",
        )

    monkeypatch.setattr(
        "events.payment_providers.MockSuccessProvider.process_payment",
        approve_after_expiry,
    )

    response = payment_request(client, booking, "expires-during-payment")

    booking.refresh_from_db()
    payment = Payment.objects.get(booking=booking)
    assert response.status_code == 402
    assert booking.status == Booking.Status.EXPIRED
    assert payment.status == Payment.Status.FAILED
    assert payment.completed_at is not None


@pytest.mark.django_db
def test_failed_payment_can_be_retried_with_a_new_idempotency_key(
    client, payment_booking, settings
):
    customer, _, booking = payment_booking
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_failure"
    failed = payment_request(client, booking, "retry-failed")
    settings.PAYMENT_PROVIDER = "mock_success"
    succeeded = payment_request(client, booking, "retry-success")

    assert failed.status_code == 402
    assert succeeded.status_code == 200
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert Payment.objects.filter(booking=booking).count() == 2
    assert Payment.objects.get(
        booking=booking, idempotency_key="retry-failed"
    ).status == Payment.Status.FAILED
    assert Payment.objects.get(
        booking=booking, idempotency_key="retry-success"
    ).status == Payment.Status.SUCCEEDED


@pytest.mark.django_db
def test_retry_resumes_processing_payment_with_its_original_provider(
    client, payment_booking, settings
):
    customer, _, booking = payment_booking
    transition_booking(
        booking_id=booking.pk,
        new_status=Booking.Status.PENDING_PAYMENT,
        actor=customer,
        reason="Payment initiated.",
    )
    pending_payment = Payment.objects.create(
        booking=booking,
        provider="mock_success",
        idempotency_key="resume-processing",
        amount=booking.total_price,
    )
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_failure"

    response = payment_request(client, booking, "resume-processing")

    pending_payment.refresh_from_db()
    booking.refresh_from_db()
    assert response.status_code == 200
    assert booking.status == Booking.Status.CONFIRMED
    assert pending_payment.provider == "mock_success"
    assert pending_payment.status == Payment.Status.SUCCEEDED
    assert Payment.objects.filter(booking=booking).count() == 1


@pytest.mark.django_db
def test_same_request_key_on_different_bookings_has_unique_provider_references(
    client, payment_booking, settings
):
    customer, event, first_booking = payment_booking
    second_event = Event.objects.create(
        organizer=event.organizer,
        name="Second payment test event",
        description="A second event used to verify provider references.",
        venue="Second Hall",
        city="Dhaka",
        event_date=date(2027, 9, 15),
        start_time=time(18, 30),
        seat_map_rows=1,
        seats_per_row=1,
        base_price=Decimal("5.00"),
    )
    second_seat = Seat.objects.get(event=second_event)
    second_hold = create_seat_holds(
        event_id=second_event.pk,
        seat_ids=[second_seat.pk],
        customer=customer,
    )[0]
    second_booking = create_booking_from_holds(
        event_id=second_event.pk,
        hold_ids=[second_hold.pk],
        customer=customer,
    )
    client.force_login(customer)
    settings.PAYMENT_PROVIDER = "mock_success"

    first_response = payment_request(client, first_booking, "shared-key")
    second_response = payment_request(client, second_booking, "shared-key")

    assert first_response.status_code == second_response.status_code == 200
    assert Payment.objects.filter(provider_reference__isnull=False).count() == 2
    assert (
        Payment.objects.filter(provider_reference__isnull=False)
        .values_list("provider_reference", flat=True)
        .distinct()
        .count()
        == 2
    )
