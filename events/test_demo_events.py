from html import unescape

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from events.demo_events import (
    DEMO_CUSTOMER_USERNAME,
    DEMO_EVENTS_DATA,
    DEMO_ORGANIZER_USERNAME,
    create_or_update_demo_data,
    create_or_update_demo_events,
    create_or_update_demo_users,
)
from events.models import Event


@pytest.mark.django_db
def test_create_or_update_demo_events_creates_four_bangladesh_routes():
    events = create_or_update_demo_events()

    assert len(events) == 4
    assert Event.objects.count() == 4

    names = [e.name for e in events]
    assert any("Dhaka → Rajshahi" in name for name in names)
    assert any("Dhaka → Chattogram" in name for name in names)
    assert any("Bhola → Sylhet" in name for name in names)
    assert any("Dhaka → Cox's Bazar" in name for name in names)

    today = timezone.localdate()
    for event in events:
        # All event dates must be in the future relative to local date
        assert event.event_date > today
        # Check seats are created
        assert event.seats.count() == event.seat_map_rows * event.seats_per_row
        # Check categories are created
        assert event.categories.filter(name="General").exists()


@pytest.mark.django_db
def test_seed_demo_events_is_idempotent():
    call_command("seed_demo_events")
    assert Event.objects.count() == 4

    # Run a second time
    call_command("seed_demo_events")
    assert Event.objects.count() == 4


@pytest.mark.django_db
def test_demo_events_appear_on_homepage_and_events_list(client):
    create_or_update_demo_events()

    # Verify on homepage
    home_response = client.get(reverse("home"))
    assert home_response.status_code == 200
    home_content = unescape(home_response.content.decode())
    for item in DEMO_EVENTS_DATA:
        assert item["name"] in home_content
        assert item["city"] in home_content

    # Verify on events list
    events_response = client.get(reverse("events:list"))
    assert events_response.status_code == 200
    events_content = unescape(events_response.content.decode())
    for item in DEMO_EVENTS_DATA:
        assert item["name"] in events_content
        assert item["city"] in events_content


@pytest.mark.django_db
def test_create_or_update_demo_users_creates_reproducible_accounts():
    users = create_or_update_demo_users()

    organizer = User.objects.get(username=DEMO_ORGANIZER_USERNAME)
    customer = User.objects.get(username=DEMO_CUSTOMER_USERNAME)

    assert organizer.role == User.Role.ORGANIZER
    assert organizer.check_password("DemoOrganizerPass123!") is True
    assert customer.role == User.Role.CUSTOMER
    assert customer.check_password("DemoCustomerPass123!") is True

    # Running again is idempotent and doesn't duplicate
    users_again = create_or_update_demo_users()
    assert User.objects.filter(username=DEMO_ORGANIZER_USERNAME).count() == 1
    assert User.objects.filter(username=DEMO_CUSTOMER_USERNAME).count() == 1


@pytest.mark.django_db
def test_seed_demo_data_command_creates_accounts_and_events_idempotently():
    call_command("seed_demo_data")
    assert Event.objects.count() == 4
    assert User.objects.filter(username=DEMO_ORGANIZER_USERNAME).exists()
    assert User.objects.filter(username=DEMO_CUSTOMER_USERNAME).exists()

    # Second run does not duplicate
    call_command("seed_demo_data")
    assert Event.objects.count() == 4
    assert User.objects.filter(username=DEMO_ORGANIZER_USERNAME).count() == 1
    assert User.objects.filter(username=DEMO_CUSTOMER_USERNAME).count() == 1
