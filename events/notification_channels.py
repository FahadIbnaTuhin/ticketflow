import logging
from typing import Protocol

from django.conf import settings
from django.core.mail import send_mail

from .models import Notification

logger = logging.getLogger(__name__)


class NotificationChannel(Protocol):
    def send(self, notification: Notification) -> bool: ...


class InAppNotificationChannel:
    def send(self, notification):
        _, created = Notification.objects.get_or_create(
            booking=notification.booking,
            notification_type=notification.notification_type,
            defaults={
                "recipient": notification.recipient,
                "message": notification.message,
            },
        )
        return created


class EmailNotificationChannel:
    def send(self, notification):
        recipient_email = notification.recipient.email
        if not recipient_email:
            return False

        subject = notification.get_notification_type_display()
        return send_mail(
            subject=subject,
            message=notification.message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],
            fail_silently=False,
        ) == 1


def dispatch_booking_notification(*, booking, notification_type, message):
    candidate = Notification(
        recipient=booking.customer,
        booking=booking,
        notification_type=notification_type,
        message=message,
    )
    channels = (InAppNotificationChannel(), EmailNotificationChannel())
    created = False

    try:
        created = channels[0].send(candidate)
    except Exception:
        logger.exception(
            "Could not persist in-app notification for booking %s.",
            booking.pk,
        )

    if not created:
        return

    try:
        channels[1].send(candidate)
    except Exception:
        logger.exception(
            "Could not send booking notification email for booking %s.",
            booking.pk,
        )
