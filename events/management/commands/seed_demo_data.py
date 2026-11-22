from django.core.management.base import BaseCommand
from events.demo_events import create_or_update_demo_data


class Command(BaseCommand):
    help = "Seed demo accounts (organizer & customer) and 4 Bangladesh demo events."

    def handle(self, *args, **options):
        result = create_or_update_demo_data()
        users = result["users"]
        events = result["events"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded demo accounts ('{users['organizer'].username}', '{users['customer'].username}') "
                f"and {len(events)} Bangladesh demo events."
            )
        )
