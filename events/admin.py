from django.contrib import admin

from .models import (
    Booking,
    BookingSeat,
    BookingStatusHistory,
    Event,
    EventCategory,
    Payment,
    Notification,
    Refund,
    Seat,
    SeatHold,
)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "city",
        "event_date",
        "start_time",
        "organizer",
        "pricing_strategy",
    )
    list_filter = ("pricing_strategy", "city", "event_date")
    search_fields = ("name", "venue", "city", "organizer__username")


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ("event", "row_number", "seat_number", "category")
    list_filter = ("event", "category")


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ("event", "name", "base_price", "early_bird_price")
    list_filter = ("event",)
    search_fields = ("event__name", "name")


@admin.register(SeatHold)
class SeatHoldAdmin(admin.ModelAdmin):
    list_display = ("seat", "customer", "status", "created_at", "expires_at")
    list_filter = ("status",)
    search_fields = ("customer__username", "seat__event__name")


class BookingSeatInline(admin.TabularInline):
    model = BookingSeat
    extra = 0
    can_delete = False
    readonly_fields = (
        "seat",
        "category",
        "category_name",
        "source_hold",
        "unit_price",
        "occupies_seat",
    )

    def has_add_permission(self, request, obj=None):
        return False


class BookingStatusHistoryInline(admin.TabularInline):
    model = BookingStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "changed_at", "actor", "reason")

    def has_add_permission(self, request, obj=None):
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = (
        "provider",
        "provider_reference",
        "idempotency_key",
        "amount",
        "status",
        "created_at",
        "updated_at",
        "completed_at",
        "processing_started_at",
        "failure_reason",
    )

    def has_add_permission(self, request, obj=None):
        return False


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    can_delete = False
    readonly_fields = (
        "amount",
        "status",
        "provider_reference",
        "created_at",
        "completed_at",
        "failure_reason",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "customer", "status", "created_at", "expires_at")
    list_filter = ("status", "event")
    search_fields = ("customer__username", "event__name")
    inlines = (
        BookingSeatInline,
        BookingStatusHistoryInline,
        PaymentInline,
    )
    readonly_fields = ("customer", "event", "status", "created_at", "updated_at", "expires_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "provider", "amount", "status", "created_at")
    list_filter = ("provider", "status")
    search_fields = ("provider_reference", "booking__customer__username")
    inlines = (RefundInline,)
    readonly_fields = (
        "booking",
        "provider",
        "provider_reference",
        "idempotency_key",
        "amount",
        "status",
        "created_at",
        "updated_at",
        "completed_at",
        "processing_started_at",
        "failure_reason",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "payment",
        "amount",
        "status",
        "provider_reference",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("provider_reference", "payment__booking__customer__username")
    readonly_fields = (
        "payment",
        "amount",
        "status",
        "provider_reference",
        "created_at",
        "completed_at",
        "failure_reason",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "recipient",
        "booking",
        "notification_type",
        "is_read",
        "created_at",
    )
    list_filter = ("notification_type", "is_read")
    search_fields = ("recipient__username", "booking__event__name", "message")
    readonly_fields = (
        "recipient",
        "booking",
        "notification_type",
        "message",
        "is_read",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
