from django.shortcuts import get_object_or_404, redirect, render
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import User

from .models import Notification


def _serialize_notification(notification):
    return {
        "id": notification.pk,
        "booking_id": notification.booking_id,
        "type": notification.notification_type,
        "message": notification.message,
        "is_read": notification.is_read,
        "created_at": notification.created_at.isoformat(),
    }


def _is_customer(user):
    return user.role == User.Role.CUSTOMER


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notification_list(request):
    if not _is_customer(request.user):
        return Response(
            {"detail": "Only customers can access notifications."},
            status=status.HTTP_403_FORBIDDEN,
        )
    notifications = (
        Notification.objects.filter(recipient=request.user)
        .select_related("booking", "booking__event")
        .order_by("-created_at")
    )
    if request.headers.get("Accept", "").startswith("text/html"):
        unread_count = notifications.filter(is_read=False).count()
        return render(
            request,
            "events/notification_list.html",
            {
                "notifications": notifications,
                "unread_count": unread_count,
            },
        )
    return Response(
        [_serialize_notification(item) for item in notifications]
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_notification_read(request, notification_id):
    if not _is_customer(request.user):
        return Response(
            {"detail": "Only customers can access notifications."},
            status=status.HTTP_403_FORBIDDEN,
        )
    notification = get_object_or_404(
        Notification.objects.filter(recipient=request.user),
        pk=notification_id,
    )
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=("is_read",))
    if request.headers.get("Accept", "").startswith("text/html"):
        return redirect("events:notification-list")
    return Response(_serialize_notification(notification))
