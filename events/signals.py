from functools import partial

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import BookingStatusHistory, Event, EventCategory, Notification, Seat


@receiver(post_save, sender=Event, dispatch_uid="events.create_seat_inventory")
def create_seat_inventory(sender, instance, created, using, **kwargs):
    if not created:
        return

    category = EventCategory.objects.using(using).create(
        event_id=instance.pk,
        name="General",
        base_price=instance.base_price,
        early_bird_price=instance.early_bird_price,
    )
    seats = []
    for row_number in range(1, instance.seat_map_rows + 1):
        for seat_number in range(1, instance.seats_per_row + 1):
            seats.append(
                Seat(
                    event_id=instance.pk,
                    category_id=category.pk,
                    row_number=row_number,
                    seat_number=seat_number,
                )
            )
            if len(seats) == 1000:
                Seat.objects.using(using).bulk_create(seats, batch_size=1000)
                seats = []

    if seats:
        Seat.objects.using(using).bulk_create(seats, batch_size=1000)


@receiver(
    post_save,
    sender=BookingStatusHistory,
    dispatch_uid="events.notify_booking_status_transitions",
)
def notify_booking_status_transition(sender, instance, created, using, **kwargs):
    if not created or instance.to_status not in (
        "confirmed",
        "cancelled",
    ):
        return

    notification_type = {
        "confirmed": Notification.Type.BOOKING_CONFIRMED,
        "cancelled": Notification.Type.BOOKING_CANCELLED,
    }[instance.to_status]
    booking_id = instance.booking_id
    transaction.on_commit(
        partial(_dispatch_booking_status_notification, booking_id, notification_type),
        using=using,
    )


def _dispatch_booking_status_notification(booking_id, notification_type):
    from .models import Booking
    from .notification_channels import dispatch_booking_notification

    booking = (
        Booking.objects.select_related("customer", "event")
        .filter(pk=booking_id)
        .first()
    )
    if booking is None:
        return

    if notification_type == Notification.Type.BOOKING_CONFIRMED:
        message = (
            f"Your booking for {booking.event.name} has been confirmed."
        )
    else:
        message = f"Your booking for {booking.event.name} has been cancelled."
    dispatch_booking_notification(
        booking=booking,
        notification_type=notification_type,
        message=message,
    )
