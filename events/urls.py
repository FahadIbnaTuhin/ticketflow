from django.urls import path

from . import views
from . import booking_views
from . import notification_views

app_name = "events"

urlpatterns = [
    path("", views.event_list, name="list"),
    path("manage/", views.organizer_event_list, name="organizer-list"),
    path("manage/create/", views.event_create, name="create"),
    path("manage/<int:pk>/edit/", views.event_update, name="update"),
    path("manage/<int:pk>/delete/", views.event_delete, name="delete"),
    path(
        "manage/<int:pk>/categories/",
        views.event_category_list,
        name="category-list",
    ),
    path(
        "manage/<int:pk>/categories/create/",
        views.event_category_create,
        name="category-create",
    ),
    path(
        "manage/<int:pk>/categories/<int:category_id>/edit/",
        views.event_category_update,
        name="category-update",
    ),
    path(
        "manage/<int:pk>/categories/<int:category_id>/delete/",
        views.event_category_delete,
        name="category-delete",
    ),
    path(
        "manage/<int:pk>/seats/<int:seat_id>/category/",
        views.assign_seat_category,
        name="seat-category-assign",
    ),
    path("bookings/", booking_views.booking_list, name="booking-list"),
    path(
        "notifications/",
        notification_views.notification_list,
        name="notification-list",
    ),
    path(
        "notifications/<int:notification_id>/read/",
        notification_views.mark_notification_read,
        name="notification-read",
    ),
    path(
        "bookings/<int:booking_id>/",
        booking_views.booking_detail,
        name="booking-detail",
    ),
    path(
        "bookings/<int:booking_id>/start-payment/",
        booking_views.start_booking_payment,
        name="booking-start-payment",
    ),
    path(
        "bookings/<int:booking_id>/cancel/",
        booking_views.cancel_booking,
        name="booking-cancel",
    ),
    path(
        "<int:event_id>/bookings/",
        booking_views.create_booking,
        name="booking-create",
    ),
    path("<int:event_id>/holds/", views.create_event_seat_holds, name="create-holds"),
    path("<int:pk>/", views.event_detail, name="detail"),
]
