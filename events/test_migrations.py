from datetime import date, time
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from .models import BookingSeat, Seat


@pytest.mark.django_db(transaction=True)
def test_migration_backfills_seats_for_existing_events(django_user_model):
    organizer = django_user_model.objects.create_user(
        username="migration-organizer",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )
    executor = MigrationExecutor(connection)
    latest_migrations = executor.loader.graph.leaf_nodes()
    pre_inventory_migration = [("events", "0001_initial")]

    try:
        executor.migrate(pre_inventory_migration)
        old_apps = MigrationExecutor(connection).loader.project_state(
            pre_inventory_migration
        ).apps
        OldEvent = old_apps.get_model("events", "Event")
        existing_event = OldEvent.objects.create(
            organizer_id=organizer.pk,
            name="Existing event",
            description="Created before seat inventory.",
            venue="Main Hall",
            city="Dhaka",
            event_date=date(2027, 1, 15),
            start_time=time(18, 30),
            seat_map_rows=2,
            seats_per_row=3,
            pricing_strategy="base",
        )

        MigrationExecutor(connection).migrate(latest_migrations)

        assert Seat.objects.filter(event_id=existing_event.pk).count() == 6
        assert set(
            Seat.objects.filter(event_id=existing_event.pk).values_list(
                "row_number", "seat_number"
            )
        ) == {
            (1, 1),
            (1, 2),
            (1, 3),
            (2, 1),
            (2, 2),
            (2, 3),
        }
    finally:
        MigrationExecutor(connection).migrate(latest_migrations)


@pytest.mark.django_db(transaction=True)
def test_category_migration_preserves_existing_booking_price_snapshots(
    django_user_model,
):
    organizer = django_user_model.objects.create_user(
        username="category-migration-organizer",
        password="test-password",
        role=django_user_model.Role.ORGANIZER,
    )
    customer = django_user_model.objects.create_user(
        username="category-migration-customer",
        password="test-password",
    )
    executor = MigrationExecutor(connection)
    latest_migrations = executor.loader.graph.leaf_nodes()
    before_category_migration = [("events", "0009_refund")]

    try:
        executor.migrate(before_category_migration)
        old_apps = MigrationExecutor(connection).loader.project_state(
            before_category_migration
        ).apps
        OldEvent = old_apps.get_model("events", "Event")
        OldSeat = old_apps.get_model("events", "Seat")
        OldSeatHold = old_apps.get_model("events", "SeatHold")
        OldBooking = old_apps.get_model("events", "Booking")
        OldBookingSeat = old_apps.get_model("events", "BookingSeat")

        old_event = OldEvent.objects.create(
            organizer_id=organizer.pk,
            name="Historical booking event",
            description="A pre-category event.",
            venue="Main Hall",
            city="Dhaka",
            event_date=date(2027, 1, 15),
            start_time=time(18, 30),
            seat_map_rows=1,
            seats_per_row=1,
            pricing_strategy="base",
            base_price=Decimal("100.00"),
        )
        old_seat = OldSeat.objects.create(
            event_id=old_event.pk,
            row_number=1,
            seat_number=1,
        )
        now = timezone.now()
        old_hold = OldSeatHold.objects.create(
            seat_id=old_seat.pk,
            customer_id=customer.pk,
            expires_at=now + timedelta(minutes=5),
        )
        old_booking = OldBooking.objects.create(
            customer_id=customer.pk,
            event_id=old_event.pk,
            status="confirmed",
            expires_at=now + timedelta(minutes=5),
        )
        OldBookingSeat.objects.create(
            booking_id=old_booking.pk,
            seat_id=old_seat.pk,
            source_hold_id=old_hold.pk,
            unit_price=Decimal("87.50"),
            occupies_seat=True,
        )

        MigrationExecutor(connection).migrate(latest_migrations)

        migrated_seat = Seat.objects.get(pk=old_seat.pk)
        migrated_booking_seat = BookingSeat.objects.get(booking_id=old_booking.pk)
        assert migrated_seat.category.name == "General"
        assert migrated_booking_seat.category_name == "General"
        assert migrated_booking_seat.unit_price == Decimal("87.50")
    finally:
        MigrationExecutor(connection).migrate(latest_migrations)
