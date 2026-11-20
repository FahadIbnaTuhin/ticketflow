from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, router, transaction
from django.utils import timezone


MAX_SEAT_MAP_DIMENSION = 200
MAX_EVENT_CAPACITY = 10_000


class Event(models.Model):
    class PricingStrategy(models.TextChoices):
        BASE = "base", "Base"
        EARLY_BIRD = "early_bird", "Early bird"

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="events",
    )
    name = models.CharField(max_length=200)
    description = models.TextField()
    venue = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    event_date = models.DateField()
    start_time = models.TimeField()
    seat_map_rows = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_SEAT_MAP_DIMENSION),
        ]
    )
    seats_per_row = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_SEAT_MAP_DIMENSION),
        ]
    )
    pricing_strategy = models.CharField(
        max_length=20,
        choices=PricingStrategy.choices,
        default=PricingStrategy.BASE,
    )
    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    early_bird_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    early_bird_starts_at = models.DateTimeField(null=True, blank=True)
    early_bird_ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("event_date", "start_time", "name")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    seat_map_rows__gte=1,
                    seat_map_rows__lte=MAX_SEAT_MAP_DIMENSION,
                    seats_per_row__gte=1,
                    seats_per_row__lte=MAX_SEAT_MAP_DIMENSION,
                ),
                name="events_event_valid_seat_dimensions",
            ),
            models.CheckConstraint(
                condition=models.Q(base_price__gte=Decimal("0.00")),
                name="events_event_nonnegative_base_price",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(early_bird_price__isnull=True)
                    | models.Q(
                        early_bird_price__gte=Decimal("0.00"),
                        early_bird_price__lte=models.F("base_price"),
                    )
                ),
                name="events_event_valid_early_bird_price",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(pricing_strategy="early_bird")
                    | models.Q(
                        early_bird_price__isnull=False,
                        early_bird_starts_at__isnull=False,
                        early_bird_ends_at__isnull=False,
                    )
                ),
                name="events_event_early_bird_configured",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(early_bird_price__isnull=True)
                        & models.Q(early_bird_starts_at__isnull=True)
                        & models.Q(early_bird_ends_at__isnull=True)
                    )
                    | (
                        models.Q(early_bird_price__isnull=False)
                        & models.Q(early_bird_starts_at__isnull=False)
                        & models.Q(early_bird_ends_at__isnull=False)
                    )
                ),
                name="events_event_early_bird_config_complete",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        early_bird_starts_at__isnull=True,
                        early_bird_ends_at__isnull=True,
                    )
                    | models.Q(
                        early_bird_starts_at__isnull=False,
                        early_bird_ends_at__isnull=False,
                        early_bird_starts_at__lt=models.F("early_bird_ends_at"),
                    )
                ),
                name="events_event_early_bird_period_ordered",
            ),
        ]

    def clean(self):
        super().clean()
        from .pricing import validate_event_pricing

        validate_event_pricing(self)
        if (
            self.seat_map_rows
            and self.seats_per_row
            and self.seat_map_rows * self.seats_per_row > MAX_EVENT_CAPACITY
        ):
            raise ValidationError(
                {
                    "seats_per_row": (
                        f"Total event capacity cannot exceed {MAX_EVENT_CAPACITY} seats."
                    )
                }
            )
        if self.pk and not self._state.adding:
            using = router.db_for_read(type(self), instance=self)
            original = (
                type(self)
                ._base_manager.using(using)
                .filter(pk=self.pk)
                .values("seat_map_rows", "seats_per_row")
                .first()
            )
            if (
                original
                and (
                    original["seat_map_rows"] != self.seat_map_rows
                    or original["seats_per_row"] != self.seats_per_row
                )
                and Seat.objects.using(using).filter(event_id=self.pk).exists()
            ):
                raise ValidationError(
                    {
                        "seat_map_rows": (
                            "Seat-map dimensions cannot change after inventory "
                            "has been created."
                        ),
                        "seats_per_row": (
                            "Seat-map dimensions cannot change after inventory "
                            "has been created."
                        ),
                    }
                )

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        from .pricing import validate_event_pricing

        validate_event_pricing(self)
        with transaction.atomic(using=using):
            if self.pk and not self._state.adding:
                original = (
                    type(self)
                    ._base_manager.using(using)
                    .select_for_update()
                    .filter(pk=self.pk)
                    .values("seat_map_rows", "seats_per_row")
                    .first()
                )
                if (
                    original
                    and (
                        original["seat_map_rows"] != self.seat_map_rows
                        or original["seats_per_row"] != self.seats_per_row
                    )
                    and Seat.objects.using(using).filter(event_id=self.pk).exists()
                ):
                    raise ValidationError(
                        "Seat-map dimensions cannot change after inventory has been created."
                    )
            return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class EventCategory(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=100)
    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    early_bird_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "name"),
                name="events_category_unique_name_per_event",
            ),
            models.CheckConstraint(
                condition=models.Q(base_price__gte=Decimal("0.00")),
                name="events_category_nonnegative_base_price",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(early_bird_price__isnull=True)
                    | models.Q(
                        early_bird_price__gte=Decimal("0.00"),
                        early_bird_price__lte=models.F("base_price"),
                    )
                ),
                name="events_category_valid_early_bird_price",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.event_id and self.event.pricing_strategy == Event.PricingStrategy.EARLY_BIRD:
            if self.early_bird_price is None:
                errors["early_bird_price"] = (
                    "An early-bird price is required for this event."
                )
        if (
            self.early_bird_price is not None
            and self.base_price is not None
            and self.early_bird_price > self.base_price
        ):
            errors["early_bird_price"] = (
                "Early-bird price cannot exceed the base price."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.event.name}: {self.name}"


class Seat(models.Model):
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="seats",
    )
    category = models.ForeignKey(
        EventCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seats",
    )
    row_number = models.PositiveSmallIntegerField()
    seat_number = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("row_number", "seat_number")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "row_number", "seat_number"),
                name="events_seat_unique_event_coordinates",
            ),
            models.CheckConstraint(
                condition=models.Q(row_number__gte=1, seat_number__gte=1),
                name="events_seat_positive_coordinates",
            ),
        ]

    def __str__(self):
        return f"{self.event}: row {self.row_number}, seat {self.seat_number}"

    def clean(self):
        super().clean()
        if self.category_id and self.category.event_id != self.event_id:
            raise ValidationError(
                {"category": "Seat category must belong to the same event."}
            )

    def save(self, *args, **kwargs):
        if self.category_id and self.category.event_id != self.event_id:
            raise ValidationError(
                {"category": "Seat category must belong to the same event."}
            )
        return super().save(*args, **kwargs)


class SeatHold(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        RELEASED = "released", "Released"
        CONVERTED = "converted", "Converted"

    seat = models.ForeignKey(
        Seat,
        on_delete=models.CASCADE,
        related_name="holds",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seat_holds",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ("created_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("seat",),
                condition=models.Q(status="active"),
                name="events_hold_one_active_per_seat",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="events_hold_expiry_after_creation",
            ),
        ]

    def __str__(self):
        return f"{self.seat} held by {self.customer}"


class Booking(models.Model):
    class Status(models.TextChoices):
        HELD = "held", "Held"
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        CONFIRMED = "confirmed", "Confirmed"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.HELD,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "held",
                        "pending_payment",
                        "confirmed",
                        "expired",
                        "cancelled",
                    ]
                ),
                name="events_booking_valid_status",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="events_booking_expiry_after_creation",
            ),
        ]

    def __str__(self):
        return f"Booking {self.pk} for {self.event}"

    @property
    def total_price(self):
        return (
            self.booking_seats.aggregate(total=models.Sum("unit_price"))["total"]
            or Decimal("0.00")
        )


class BookingSeat(models.Model):
    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="booking_seats",
    )
    seat = models.ForeignKey(
        Seat,
        on_delete=models.PROTECT,
        related_name="booking_seats",
    )
    category = models.ForeignKey(
        EventCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="booking_seats",
    )
    category_name = models.CharField(max_length=100, blank=True)
    source_hold = models.OneToOneField(
        SeatHold,
        on_delete=models.PROTECT,
        related_name="booking_seat",
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    occupies_seat = models.BooleanField(default=True)

    class Meta:
        ordering = ("seat__row_number", "seat__seat_number")
        constraints = [
            models.UniqueConstraint(
                fields=("booking", "seat"),
                name="events_booking_seat_unique",
            ),
            models.UniqueConstraint(
                fields=("seat",),
                condition=models.Q(occupies_seat=True),
                name="events_one_active_booking_per_seat",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=Decimal("0.00")),
                name="events_booking_seat_nonnegative_price",
            ),
        ]

    def __str__(self):
        return f"{self.seat} in booking {self.booking_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous_price = (
                type(self)
                ._base_manager.using(kwargs.get("using") or self._state.db)
                .filter(pk=self.pk)
                .values("unit_price", "category_id", "category_name")
                .first()
            )
            if previous_price is not None and (
                self.unit_price != previous_price["unit_price"]
                or self.category_id != previous_price["category_id"]
                or self.category_name != previous_price["category_name"]
            ):
                raise ValidationError(
                    "A booking seat's price and category snapshots are immutable."
                )
        return super().save(*args, **kwargs)

class BookingStatusHistory(models.Model):
    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="status_history",
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, choices=Booking.Status.choices)
    changed_at = models.DateTimeField(default=timezone.now, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="booking_status_changes",
    )
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ("changed_at", "pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    to_status__in=[
                        "held",
                        "pending_payment",
                        "confirmed",
                        "expired",
                        "cancelled",
                    ]
                ),
                name="events_booking_history_valid_status",
            ),
        ]

    def __str__(self):
        return f"Booking {self.booking_id}: {self.from_status or 'created'} -> {self.to_status}"


class Payment(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    provider = models.CharField(max_length=50)
    provider_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
    )
    idempotency_key = models.CharField(max_length=255)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("booking", "idempotency_key"),
                name="events_payment_booking_idempotency_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=Decimal("0.00")),
                name="events_payment_nonnegative_amount",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["processing", "succeeded", "failed"]
                ),
                name="events_payment_valid_status",
            ),
        ]

    def __str__(self):
        return f"Payment {self.pk} for booking {self.booking_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self)
                ._base_manager.using(kwargs.get("using") or self._state.db)
                .filter(pk=self.pk)
                .values("amount", "provider", "idempotency_key")
                .first()
            )
            if previous and (
                self.amount != previous["amount"]
                or self.provider != previous["provider"]
                or self.idempotency_key != previous["idempotency_key"]
            ):
                raise ValidationError(
                    "Payment amount, provider, and idempotency key are immutable."
                )
        return super().save(*args, **kwargs)


class Refund(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    payment = models.OneToOneField(
        Payment,
        on_delete=models.PROTECT,
        related_name="refund",
    )
    provider_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("created_at", "pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=Decimal("0.00")),
                name="events_refund_nonnegative_amount",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["processing", "succeeded", "failed"]
                ),
                name="events_refund_valid_status",
            ),
        ]

    def __str__(self):
        return f"Refund {self.pk} for payment {self.payment_id}"


class Notification(models.Model):
    class Type(models.TextChoices):
        BOOKING_CONFIRMED = "booking_confirmed", "Booking confirmed"
        BOOKING_CANCELLED = "booking_cancelled", "Booking cancelled"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.PROTECT,
        related_name="notifications",
    )
    notification_type = models.CharField(max_length=32, choices=Type.choices)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("booking", "notification_type"),
                name="events_notification_booking_type_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    notification_type__in=[
                        "booking_confirmed",
                        "booking_cancelled",
                    ]
                ),
                name="events_notification_valid_type",
            ),
        ]

    def __str__(self):
        return f"{self.get_notification_type_display()} for booking {self.booking_id}"
