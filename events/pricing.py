from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Event, EventCategory


def validate_event_pricing(event):
    errors = {}
    if event.base_price is not None and event.base_price < Decimal("0.00"):
        errors["base_price"] = "Base price cannot be negative."

    early_values = (
        event.early_bird_price,
        event.early_bird_starts_at,
        event.early_bird_ends_at,
    )
    has_early_values = any(value is not None for value in early_values)
    if event.pricing_strategy == Event.PricingStrategy.EARLY_BIRD and not all(
        value is not None for value in early_values
    ):
        errors["early_bird_price"] = (
            "Early-bird price and eligibility start/end are required."
        )
    elif has_early_values and not all(value is not None for value in early_values):
        errors["early_bird_price"] = (
            "Configure the early-bird price, start, and end together."
        )

    if event.early_bird_price is not None:
        if event.early_bird_price < Decimal("0.00"):
            errors["early_bird_price"] = "Early-bird price cannot be negative."
        elif (
            event.base_price is not None
            and event.early_bird_price > event.base_price
        ):
            errors["early_bird_price"] = (
                "Early-bird price cannot exceed the base price."
            )

    starts_at = event.early_bird_starts_at
    ends_at = event.early_bird_ends_at
    if starts_at is not None and ends_at is not None:
        if starts_at >= ends_at:
            errors["early_bird_ends_at"] = (
                "Early-bird eligibility must end after it starts."
            )

        if event.event_date and event.start_time:
            event_starts_at = datetime.combine(event.event_date, event.start_time)
            if timezone.is_aware(starts_at):
                event_starts_at = timezone.make_aware(
                    event_starts_at,
                    timezone.get_current_timezone(),
                )
            if ends_at >= event_starts_at:
                errors["early_bird_ends_at"] = (
                    "Early-bird eligibility must end before the event starts."
                )

    if errors:
        raise ValidationError(errors)


def calculate_seat_price(event, *, category: EventCategory | None = None, at=None):
    at = at or timezone.now()
    if category is not None:
        if category.event_id != event.pk:
            raise ValidationError("Pricing category must belong to the event.")
        base_price = category.base_price
        early_bird_price = category.early_bird_price
        if base_price is None or base_price < Decimal("0.00"):
            raise ValidationError("Category base price must be nonnegative.")
        if (
            early_bird_price is not None
            and (
                early_bird_price < Decimal("0.00")
                or early_bird_price > base_price
            )
        ):
            raise ValidationError("Category early-bird price is invalid.")
    else:
        base_price = event.base_price
        early_bird_price = event.early_bird_price

    if event.pricing_strategy == Event.PricingStrategy.EARLY_BIRD:
        validate_event_pricing(event)
        if early_bird_price is None:
            raise ValidationError("An early-bird price is required for this category.")
        if event.early_bird_starts_at <= at < event.early_bird_ends_at:
            return early_bird_price
    return base_price
