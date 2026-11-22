from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from events.models import Event


@pytest.fixture
def organizer(django_user_model):
    return django_user_model.objects.create_user(
        username="browse-organizer",
        password="StrongPassword123!",
        role=django_user_model.Role.ORGANIZER,
    )


@pytest.fixture
def customer(django_user_model):
    return django_user_model.objects.create_user(
        username="browse-customer",
        password="StrongPassword123!",
        role=django_user_model.Role.CUSTOMER,
    )


@pytest.fixture
def sample_events(organizer):
    today = timezone.localdate()
    now = timezone.now()
    standard_event = Event.objects.create(
        organizer=organizer,
        name="Standard Jazz Evening",
        description="A smooth jazz session with local artists.",
        venue="Blue Note Lounge",
        city="Dhaka",
        event_date=today + timedelta(days=7),
        start_time=time(19, 30),
        seat_map_rows=4,
        seats_per_row=8,
        pricing_strategy=Event.PricingStrategy.BASE,
        base_price=Decimal("35.00"),
    )
    early_bird_event = Event.objects.create(
        organizer=organizer,
        name="Electronic Waves Festival",
        description="An energetic electronic dance music night.",
        venue="Expo Arena",
        city="Sylhet",
        event_date=today + timedelta(days=20),
        start_time=time(21, 0),
        seat_map_rows=5,
        seats_per_row=10,
        pricing_strategy=Event.PricingStrategy.EARLY_BIRD,
        base_price=Decimal("60.00"),
        early_bird_price=Decimal("40.00"),
        early_bird_starts_at=now - timedelta(days=2),
        early_bird_ends_at=now + timedelta(days=5),
    )
    return standard_event, early_bird_event


@pytest.mark.django_db
def test_browse_page_uses_base_template(client):
    response = client.get(reverse("events:list"))

    assert response.status_code == 200
    templates = [t.name for t in response.templates if t.name]
    assert "events/event_list.html" in templates
    assert "base.html" in templates


@pytest.mark.django_db
def test_browse_page_displays_pricing_and_detail_links(client, sample_events):
    standard, early_bird = sample_events
    response = client.get(reverse("events:list"))

    assert response.status_code == 200
    content = response.content.decode()

    # Standard event details
    assert "Standard Jazz Evening" in content
    assert "$35.00" in content
    assert reverse("events:detail", args=[standard.pk]) in content

    # Early bird event details
    assert "Electronic Waves Festival" in content
    assert "$40.00" in content
    assert "$60.00" in content
    assert "Early Bird" in content
    assert reverse("events:detail", args=[early_bird.pk]) in content


@pytest.mark.django_db
def test_browse_page_displays_active_filter_pills_and_clear_link(client, sample_events):
    response = client.get(reverse("events:list"), {"q": "Jazz", "city": "Dhaka"})

    assert response.status_code == 200
    content = response.content.decode()
    assert "Active Filters:" in content
    assert "Keyword:" in content
    assert "Jazz" in content
    assert "City:" in content
    assert "Dhaka" in content
    assert "Clear all filters" in content


@pytest.mark.django_db
def test_browse_page_empty_state_when_filters_do_not_match(client, sample_events):
    response = client.get(reverse("events:list"), {"q": "NonExistentEventName123"})

    assert response.status_code == 200
    content = response.content.decode()
    assert "No events match your search." in content
    assert "Clear all filters" in content


@pytest.mark.django_db
def test_browse_page_empty_state_when_no_events_in_catalog(client):
    response = client.get(reverse("events:list"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "No events match your search." in content


@pytest.mark.django_db
def test_browse_page_organizer_controls(client, organizer, customer):
    # Anonymous
    anon_response = client.get(reverse("events:list"))
    assert "Manage my events" not in anon_response.content.decode()

    # Customer
    client.force_login(customer)
    cust_response = client.get(reverse("events:list"))
    assert "Manage my events" not in cust_response.content.decode()

    # Organizer
    client.force_login(organizer)
    org_response = client.get(reverse("events:list"))
    assert "Manage my events" in org_response.content.decode()
