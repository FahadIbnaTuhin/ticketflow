# TicketFlow

TicketFlow is an event ticket booking platform built with Django and Django REST
Framework.

## Requirements

- Python 3.10 or newer
- Git
- PostgreSQL for PostgreSQL-backed development (optional initially; SQLite is
  used by default)

## Local setup

From the project directory:

```bash
git clone <repository-url>
cd tickerflow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `DJANGO_SECRET_KEY` to a unique, random value before using
the application outside local development. The `.env` file is ignored by Git.

Apply database migrations and start Django:

```bash
python manage.py migrate
python manage.py runserver
```

The health endpoint is available at <http://127.0.0.1:8000/health/>.

Registration and login are available at `/accounts/register/` and
`/accounts/login/`. Public registration creates Customer accounts. To assign
the Organizer role during development, create a superuser with
`python manage.py createsuperuser`, open `/admin/`, and change the user's role.
Events can be browsed at `/events/`. Organizers can manage their own events at
`/events/manage/`. Authenticated customers can hold seats by sending a JSON
`POST` request to `/events/<event_id>/holds/` with a body such as
`{"seat_ids": [1, 2]}`. Holds expire after five minutes.

Customers can convert their active holds into a booking with
`POST /events/<event_id>/bookings/` and `{"hold_ids": [1, 2]}`. View personal
bookings at `GET /events/bookings/` or `GET /events/bookings/<booking_id>/`.
Use `POST /events/bookings/<booking_id>/start-payment/` with an
`Idempotency-Key` header to initiate payment, and
`POST /events/bookings/<booking_id>/cancel/` to cancel a booking. A successful
mock payment moves the booking through Pending Payment to Confirmed; a
declined payment leaves it Pending Payment so another attempt can be made with
a new idempotency key.

Organizers choose each event's pricing strategy and early-bird eligibility
window in the event form, then manage seat categories at
`/events/manage/<event_id>/categories/`. Categories have independent base and,
for EARLY_BIRD events, early-bird prices; seats are assigned to a category from
the category management page. Customers see seat categories and their
applicable prices on the event detail seat map. The early-bird price applies
from the configured start timestamp (inclusive) until the end timestamp
(exclusive); otherwise, the category base price applies. Booking creation
stores immutable category and price snapshots per seat, and the booking API
returns each seat's category and `unit_price` plus the derived `total_price`.
Existing events and seats are migrated into a `General` category without
changing any historical booking price snapshots.

When the pricing migration is first applied, events that previously selected
EARLY_BIRD are switched to BASE because those records had no early-bird price
or eligibility window to preserve.

## Mock payments

Set `PAYMENT_PROVIDER` in `.env` to `mock_success` (the default) or
`mock_failure`. Both providers run locally without credentials, network calls,
or real transactions. The success provider simulates approval; the failure
provider simulates a decline. The start-payment endpoint requires a unique
request `Idempotency-Key`; it uses the booking's stored seat-price snapshots,
not any client-supplied amount. Repeating the same key returns the existing
payment result instead of creating or confirming a duplicate payment.

Set `DJANGO_ENV=production` in production deployments. By default,
`ALLOW_MOCK_PAYMENTS_IN_PRODUCTION=False` rejects both mock providers in that
environment, so payment and refund requests fail closed. For the hosted
assignment demo only, set `ALLOW_MOCK_PAYMENTS_IN_PRODUCTION=True` to opt in to
the simulated success/failure outcomes. This option enables simulations only:
it does not process real payments or refunds. Do not enable it for real-money
sales. Setting `DEBUG=False` alone does not select the production environment;
configure `DJANGO_ENV` explicitly.

## Notifications

Customers receive an in-app notification and an email when a booking is
confirmed after successful payment or cancelled. No notifications are sent for
holds, pending payment, or declined payments. Email uses Django's console
backend by default, so messages appear in the application output without
external email credentials. Set `EMAIL_BACKEND` and `DEFAULT_FROM_EMAIL` in
`.env` to adjust the Django email configuration. Email delivery errors are
logged and do not roll back booking changes or the in-app notification.

Authenticated customers can list their notifications at
`GET /events/notifications/` and mark one as read with
`POST /events/notifications/<notification_id>/read/`. These endpoints are
scoped to the signed-in customer.

## Cancellation and refunds

Customers may cancel their own bookings through
`POST /events/bookings/<booking_id>/cancel/`. A confirmed booking cancelled
more than 24 hours before the event's local start time receives a full refund
of its successful payment. Cancellations exactly 24 hours before or closer to
the start receive no refund. Refund records are linked to payments and limited
to one per payment; the cancellation response includes `refund.issued` and
`refund.amount`. Refund execution uses the selected mock payment provider only.

## PostgreSQL

Install and start PostgreSQL, create a database and user, then set the following
values in `.env`:

```dotenv
DB_ENGINE=postgresql
DB_NAME=ticketflow
DB_USER=ticketflow
DB_PASSWORD=your-local-database-password
DB_HOST=localhost
DB_PORT=5432
```

Run `python manage.py migrate` after changing the database configuration.

## Checks and tests

```bash
python manage.py check
pytest
```
