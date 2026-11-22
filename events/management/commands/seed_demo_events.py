from django.core.management.base import BaseCommand
from events.demo_events import create_or_update_demo_events


class Command(BaseCommand):
    help = "Seed 4 upcoming Bangladesh demo events for TicketFlow."

    def handle(self, *args, **options):
        events = create_or_update_demo_events()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {len(events)} Bangladesh demo events."
            )
        )
