import os
from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import Event, EventCategory

User = get_user_model()

DEMO_ORGANIZER_USERNAME = "demo_organizer"
DEMO_ORGANIZER_EMAIL = "organizer@ticketflow.demo"
DEMO_CUSTOMER_USERNAME = "demo_customer"
DEMO_CUSTOMER_EMAIL = "customer@ticketflow.demo"

DEMO_EVENTS_DATA = [
    {
        "name": "Dhaka → Rajshahi Silk City Classical & Folk Night",
        "description": (
            "Experience an enchanting musical voyage along the historic Dhaka to Rajshahi silk route. "
            "Featuring renowned sitar maestros, Baul folk ensembles, and regional Rajshahi mango and silk exhibits."
        ),
        "venue": "Padma Riverside Convention Hall, Rajshahi",
        "city": "Rajshahi",
        "days_ahead": 5,
        "start_time": time(18, 30),
        "seat_map_rows": 8,
        "seats_per_row": 12,
        "pricing_strategy": Event.PricingStrategy.BASE,
        "base_price": Decimal("500.00"),
        "early_bird_price": None,
        "vip_category": {
            "name": "VIP Silk Tier",
            "base_price": Decimal("900.00"),
            "early_bird_price": None,
        },
    },
    {
        "name": "Dhaka → Chattogram Coastal Rock Carnival",
        "description": (
            "The premier concert caravan traveling from Dhaka to the port city of Chattogram. "
            "High-voltage live rock sets, coastal youth culture showcases, and local culinary delights."
        ),
        "venue": "GEC Convention Center, Chattogram",
        "city": "Chattogram",
        "days_ahead": 12,
        "start_time": time(19, 0),
        "seat_map_rows": 10,
        "seats_per_row": 15,
        "pricing_strategy": Event.PricingStrategy.EARLY_BIRD,
        "base_price": Decimal("800.00"),
        "early_bird_price": Decimal("600.00"),
        "early_bird_window_days": (2, 8),
        "vip_category": {
            "name": "VIP Front Stage",
            "base_price": Decimal("1400.00"),
            "early_bird_price": Decimal("1100.00"),
        },
    },
    {
        "name": "Bhola → Sylhet Island-to-Highlands Sufi Gala",
        "description": (
            "A cross-country cultural extravaganza linking the island heritage of Bhola to the serene green tea hills of Sylhet. "
            "An evening of transcendent spiritual Qawwali, flute recitals, and Bengali Sufi poetry."
        ),
        "venue": "Surma Riverfront Auditorium, Sylhet",
        "city": "Sylhet",
        "days_ahead": 18,
        "start_time": time(17, 30),
        "seat_map_rows": 6,
        "seats_per_row": 10,
        "pricing_strategy": Event.PricingStrategy.BASE,
        "base_price": Decimal("400.00"),
        "early_bird_price": None,
        "vip_category": {
            "name": "VIP Tea Garden Terrace",
            "base_price": Decimal("750.00"),
            "early_bird_price": None,
        },
    },
    {
        "name": "Dhaka → Cox's Bazar Sunset Waves Beach Concert",
        "description": (
            "From the vibrant capital of Dhaka to the world's longest natural sea beach in Cox's Bazar. "
            "Enjoy sunset acoustic sets followed by an electrifying beachfront electronic and fusion festival beside the Bay of Bengal."
        ),
        "venue": "Laboni Beach Open-Air Stage, Cox's Bazar",
        "city": "Cox's Bazar",
        "days_ahead": 25,
        "start_time": time(16, 30),
        "seat_map_rows": 10,
        "seats_per_row": 20,
        "pricing_strategy": Event.PricingStrategy.EARLY_BIRD,
        "base_price": Decimal("1200.00"),
        "early_bird_price": Decimal("950.00"),
        "early_bird_window_days": (1, 14),
        "vip_category": {
            "name": "VIP Beachfront Deck",
            "base_price": Decimal("2000.00"),
            "early_bird_price": Decimal("1650.00"),
        },
    },
]


def create_or_update_demo_events():
    """
    Creates or updates the 4 Bangladesh demo events idempotently.
    Ensures all event dates are in the future relative to local date.
    Does not delete or overwrite existing user data or unrelated events.
    """
    today = timezone.localdate()
    now = timezone.now()

    organizer = User.objects.filter(username="bangladesh_events").first()
    if organizer is None:
        organizer = User.objects.create_user(
            username="bangladesh_events",
            email="organizer@ticketflow.local",
            password="DemoPassword123!",
            role=User.Role.ORGANIZER,
        )
    elif organizer.role != User.Role.ORGANIZER:
        organizer.role = User.Role.ORGANIZER
        organizer.save(update_fields=["role"])

    created_or_updated = []
    for item in DEMO_EVENTS_DATA:
        event_date = today + timedelta(days=item["days_ahead"])
        early_bird_starts_at = None
        early_bird_ends_at = None
        if item["pricing_strategy"] == Event.PricingStrategy.EARLY_BIRD:
            start_offset, end_offset = item["early_bird_window_days"]
            early_bird_starts_at = now - timedelta(days=start_offset)
            early_bird_ends_at = now + timedelta(days=end_offset)

        event = Event.objects.filter(name=item["name"]).first()
        if event is None:
            event = Event.objects.create(
                organizer=organizer,
                name=item["name"],
                description=item["description"],
                venue=item["venue"],
                city=item["city"],
                event_date=event_date,
                start_time=item["start_time"],
                seat_map_rows=item["seat_map_rows"],
                seats_per_row=item["seats_per_row"],
                pricing_strategy=item["pricing_strategy"],
                base_price=item["base_price"],
                early_bird_price=item["early_bird_price"],
                early_bird_starts_at=early_bird_starts_at,
                early_bird_ends_at=early_bird_ends_at,
            )
        else:
            event.event_date = event_date
            event.start_time = item["start_time"]
            event.venue = item["venue"]
            event.city = item["city"]
            event.description = item["description"]
            event.pricing_strategy = item["pricing_strategy"]
            event.base_price = item["base_price"]
            event.early_bird_price = item["early_bird_price"]
            event.early_bird_starts_at = early_bird_starts_at
            event.early_bird_ends_at = early_bird_ends_at
            event.save()

        # Update or create VIP category if specified
        if "vip_category" in item:
            vip_info = item["vip_category"]
            vip_cat, _ = EventCategory.objects.get_or_create(
                event=event,
                name=vip_info["name"],
                defaults={
                    "base_price": vip_info["base_price"],
                    "early_bird_price": vip_info["early_bird_price"],
                },
            )
            # Reassign row 1 seats to VIP tier
            event.seats.filter(row_number=1).update(category=vip_cat)

        created_or_updated.append(event)

    return created_or_updated


def create_or_update_demo_users():
    """
    Creates or updates reproducible demo accounts for Organizer and Customer.
    Passwords can be overridden via DEMO_ORGANIZER_PASSWORD and DEMO_CUSTOMER_PASSWORD environment variables.
    Idempotent: updates existing accounts without creating duplicate records.
    """
    organizer_password = os.getenv("DEMO_ORGANIZER_PASSWORD", "DemoOrganizerPass123!")
    customer_password = os.getenv("DEMO_CUSTOMER_PASSWORD", "DemoCustomerPass123!")

    # 1. Demo Organizer
    organizer, created = User.objects.get_or_create(
        username=DEMO_ORGANIZER_USERNAME,
        defaults={
            "email": DEMO_ORGANIZER_EMAIL,
            "role": User.Role.ORGANIZER,
        },
    )
    if not created:
        organizer.email = DEMO_ORGANIZER_EMAIL
        organizer.role = User.Role.ORGANIZER
    organizer.set_password(organizer_password)
    organizer.save()

    # 2. Demo Customer
    customer, created = User.objects.get_or_create(
        username=DEMO_CUSTOMER_USERNAME,
        defaults={
            "email": DEMO_CUSTOMER_EMAIL,
            "role": User.Role.CUSTOMER,
        },
    )
    if not created:
        customer.email = DEMO_CUSTOMER_EMAIL
        customer.role = User.Role.CUSTOMER
    customer.set_password(customer_password)
    customer.save()

    # 3. Ensure legacy bangladesh_events organizer also exists and has correct role
    legacy_org = User.objects.filter(username="bangladesh_events").first()
    if legacy_org is None:
        legacy_org = User.objects.create_user(
            username="bangladesh_events",
            email="organizer@ticketflow.local",
            password=organizer_password,
            role=User.Role.ORGANIZER,
        )
    elif legacy_org.role != User.Role.ORGANIZER:
        legacy_org.role = User.Role.ORGANIZER
        legacy_org.save(update_fields=["role"])

    return {
        "organizer": organizer,
        "customer": customer,
        "legacy_organizer": legacy_org,
    }


def create_or_update_demo_data():
    """
    Idempotently creates both demo accounts and Bangladesh demo events.
    """
    users = create_or_update_demo_users()
    events = create_or_update_demo_events()
    return {"users": users, "events": events}
