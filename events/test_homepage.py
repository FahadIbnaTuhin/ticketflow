from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from events.models import Event

User = get_user_model()


@pytest.fixture
def organizer(django_user_model):
    return django_user_model.objects.create_user(
        username="organizer-user",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )


@pytest.fixture
def customer(django_user_model):
    return django_user_model.objects.create_user(
        username="customer-user",
        password="StrongPassword123!",
        role=django_user_model.Role.CUSTOMER,
    )


@pytest.fixture
def future_event(organizer):
    today = timezone.localdate()
    return Event.objects.create(
        organizer=organizer,
        name="Summer Indie Rock Festival",
        description="An unforgettable night of indie rock performances and local food trucks.",
        venue="Green Valley Amphitheater",
        city="Dhaka",
        event_date=today + timedelta(days=14),
        start_time=time(19, 0),
        seat_map_rows=5,
        seats_per_row=10,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("45.00"),
    )


@pytest.mark.django_db
def test_homepage_url_resolves_and_named_route_works():
    assert reverse("home") == "/"


@pytest.mark.django_db
def test_homepage_returns_http_200_for_anonymous_user(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "TicketFlow" in response.content.decode()
    templates = [t.name for t in response.templates if t.name]
    assert "home.html" in templates
    assert "base.html" in templates


@pytest.mark.django_db
def test_homepage_displays_upcoming_events(client, future_event):
    response = client.get("/")

    assert response.status_code == 200
    assert future_event in response.context["events"]
    content = response.content.decode()
    assert "Summer Indie Rock Festival" in content
    assert "Green Valley Amphitheater" in content
    assert "Dhaka" in content
    assert "$45.00" in content


@pytest.mark.django_db
def test_homepage_excludes_past_events(client, organizer, future_event):
    today = timezone.localdate()
    past_event = Event.objects.create(
        organizer=organizer,
        name="Past Acoustic Night",
        description="A past performance from last month.",
        venue="Old Town Cafe",
        city="Dhaka",
        event_date=today - timedelta(days=10),
        start_time=time(20, 0),
        seat_map_rows=2,
        seats_per_row=4,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("20.00"),
    )

    response = client.get("/")

    assert response.status_code == 200
    events_in_context = list(response.context["events"])
    assert future_event in events_in_context
    assert past_event not in events_in_context
    content = response.content.decode()
    assert "Summer Indie Rock Festival" in content
    assert "Past Acoustic Night" not in content


@pytest.mark.django_db
def test_homepage_empty_state_when_no_events_exist(client):
    response = client.get("/")

    assert response.status_code == 200
    assert len(response.context["events"]) == 0
    content = response.content.decode()
    assert "No Upcoming Events Found" in content
    assert reverse("events:list") in content


@pytest.mark.django_db
def test_homepage_event_cards_link_to_event_detail(client, future_event):
    response = client.get("/")

    assert response.status_code == 200
    detail_url = reverse("events:detail", args=[future_event.pk])
    assert detail_url in response.content.decode()


@pytest.mark.django_db
def test_homepage_displays_early_bird_pricing(client, organizer):
    today = timezone.localdate()
    now = timezone.now()
    early_bird_event = Event.objects.create(
        organizer=organizer,
        name="Tech Innovators Summit",
        description="Keynotes from industry leaders.",
        venue="Grand Convention Hall",
        city="Chittagong",
        event_date=today + timedelta(days=30),
        start_time=time(10, 0),
        seat_map_rows=4,
        seats_per_row=6,
        pricing_strategy=Event.PricingStrategy.EARLY_BIRD,
        base_price=Decimal("100.00"),
        early_bird_price=Decimal("65.00"),
        early_bird_starts_at=now - timedelta(days=1),
        early_bird_ends_at=now + timedelta(days=7),
    )

    response = client.get("/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "Tech Innovators Summit" in content
    assert "Early Bird" in content
    assert "$65.00" in content
    assert "$100.00" in content


@pytest.mark.django_db
def test_homepage_navigation_for_anonymous_user(client):
    response = client.get("/")

    assert response.status_code == 200
    content = response.content.decode()
    assert reverse("accounts:login") in content
    assert reverse("accounts:register") in content
    assert reverse("events:list") in content
    # Should not show protected organizer/customer links in nav
    assert reverse("events:organizer-list") not in content
    assert reverse("events:booking-list") not in content


@pytest.mark.django_db
def test_homepage_navigation_for_authenticated_customer(client, customer):
    client.force_login(customer)
    response = client.get("/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "customer-user" in content
    assert reverse("events:booking-list") in content
    assert reverse("events:notification-list") in content
    assert reverse("accounts:logout") in content
    # Should not show organizer event management link
    assert reverse("events:organizer-list") not in content


@pytest.mark.django_db
def test_homepage_navigation_for_authenticated_organizer(client, organizer):
    client.force_login(organizer)
    response = client.get("/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "organizer-user" in content
    assert reverse("events:organizer-list") in content
    assert reverse("events:create") in content
    assert reverse("accounts:logout") in content


@pytest.mark.django_db
def test_existing_event_list_and_detail_routes_unaffected(client, future_event):
    list_response = client.get(reverse("events:list"))
    assert list_response.status_code == 200
    assert future_event in list_response.context["events"]

    detail_response = client.get(reverse("events:detail", args=[future_event.pk]))
    assert detail_response.status_code == 200
    assert detail_response.context["event"] == future_event
