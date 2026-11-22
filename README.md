# TicketFlow

[![CI/CD](https://github.com/FahadIbnaTuhin/ticketflow/actions/workflows/ci.yml/badge.svg)](https://github.com/FahadIbnaTuhin/ticketflow/actions/workflows/ci.yml)

TicketFlow is a high-reliability event ticketing and seat reservation platform built with Django, Django REST Framework, Bootstrap 5, and PostgreSQL. It features real-time seat holding with 5-minute expiries, idempotent payment processing, atomic booking workflows, transactional cancellations with a 24-hour refund cutoff, and customer in-app/email notifications.

---

## Quick Demo Credentials & Seeding

TicketFlow includes a reproducible, idempotent management command to seed demo accounts and 4 realistic Bangladesh demo events.

To seed or refresh demo data:

```bash
python manage.py seed_demo_data
```

### Pre-Configured Demo Accounts

| Role | Username | Password (default) | Email | Permissions |
| :--- | :--- | :--- | :--- | :--- |
| **Organizer** | `demo_organizer` | `DemoOrganizerPass123!` | `organizer@ticketflow.demo` | Create/edit events, configure seat grid & categories, assign seat tiers |
| **Customer** | `demo_customer` | `DemoCustomerPass123!` | `customer@ticketflow.demo` | Browse events, select seats, hold seats, pay, cancel, view notifications |

> **Security Note:** Passwords can be safely overridden in your environment or `.env` using `DEMO_ORGANIZER_PASSWORD` and `DEMO_CUSTOMER_PASSWORD`. The command never hardcodes production secrets and runs idempotently without creating duplicate records.

---

## 4 Bangladesh Demo Events

Running `python manage.py seed_demo_data` automatically configures 4 upcoming events with future dates, seat grids, and VIP seat tiers:

1. **Dhaka → Rajshahi Silk City Classical & Folk Night** (Padma Riverside Convention Hall, Rajshahi — Base pricing)
2. **Dhaka → Chattogram Coastal Rock Carnival** (GEC Convention Center, Chattogram — Early-bird pricing)
3. **Bhola → Sylhet Island-to-Highlands Sufi Gala** (Surma Riverfront Auditorium, Sylhet — Base pricing)
4. **Dhaka → Cox's Bazar Sunset Waves Beach Concert** (Laboni Beach Open-Air Stage, Cox's Bazar — Early-bird pricing)

---

## Requirements

- Python 3.10, 3.11, or 3.12
- Git
- SQLite (default for local development) or PostgreSQL (used in CI & Production)

---

## Local Setup

```bash
# 1. Clone repository
git clone https://github.com/FahadIbnaTuhin/ticketflow.git
cd ticketflow

# 2. Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
python -m pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env and set a unique DJANGO_SECRET_KEY

# 5. Apply migrations and seed demo data
python manage.py migrate
python manage.py seed_demo_data

# 6. Start development server
python manage.py runserver
```

- **Homepage:** <http://127.0.0.1:8000/>
- **Browse Events:** <http://127.0.0.1:8000/events/>
- **Login / Register:** <http://127.0.0.1:8000/accounts/login/>
- **Notification Center:** <http://127.0.0.1:8000/events/notifications/>
- **Organizer Dashboard:** <http://127.0.0.1:8000/events/manage/>
- **Health Check:** <http://127.0.0.1:8000/health/>

---

## Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `DJANGO_SECRET_KEY` | Django cryptographic signing secret | Required |
| `DJANGO_DEBUG` | Enable debug mode (`True`/`False`) | `False` |
| `DJANGO_ENV` | Environment name (`development`, `test`, `production`) | `development` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hostnames | `127.0.0.1,localhost` |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins for CSRF | Empty |
| `DB_ENGINE` | Database engine (`sqlite` or `postgresql`) | `sqlite` |
| `DB_NAME` | Database name | `ticketflow` |
| `DB_USER` | Database username | `ticketflow` |
| `DB_PASSWORD` | Database password | Empty |
| `DB_HOST` | Database host | `localhost` |
| `DB_PORT` | Database port | `5432` |
| `DB_SSLMODE` | PostgreSQL SSL mode (`require`, `prefer`, etc.) | Empty |
| `PAYMENT_PROVIDER` | Mock payment simulator (`mock_success` or `mock_failure`) | `mock_success` |
| `ALLOW_MOCK_PAYMENTS_IN_PRODUCTION` | Allow simulated mock payments in production demo | `False` |
| `EMAIL_BACKEND` | Django email backend class | `django.core.mail.backends.console.EmailBackend` |
| `DEFAULT_FROM_EMAIL` | Default sender email address | `no-reply@ticketflow.local` |

---

## Pricing Strategies & Seat Categories

TicketFlow provides flexible and mathematically validated pricing models:

1. **`BASE` Strategy (`Event.PricingStrategy.BASE`)**:
   Standard pricing model where every seat is priced according to its assigned category base price (or the event's base price).
2. **`EARLY_BIRD` Strategy (`Event.PricingStrategy.EARLY_BIRD`)**:
   Dynamic time-window model. Between `early_bird_starts_at` (inclusive) and `early_bird_ends_at` (exclusive), customers receive the discounted `early_bird_price`. Once the window elapses or prior to opening, pricing automatically reverts to `base_price`.
3. **Multi-Tier / Category Pricing (`EventCategory`)**:
   Organizers can create multiple seating tiers (e.g., *VIP*, *Premium*, *General*) for an event, configure category-specific base and early-bird rates, and assign specific seat rows to each category.
   *(Note: Tiered pricing is achieved through `EventCategory` models and seat relationships, rather than a separate `TIERED` enum value in `PricingStrategy`).*

---

## Core Business Workflows

### 1. Real-Time Seat Holding (5-Minute Expiry)
- Customers select interactive seats from the visual SVG seat map.
- Submitting selection triggers `POST /events/<id>/holds/` wrapped in atomic transactions with PostgreSQL row-level locks (`select_for_update`).
- Conflicting holds return HTTP 409 Conflict.
- Active holds display a live 5-minute countdown timer. Expired holds cannot be converted to bookings.

### 2. Booking Creation & Price Snapshots
- Converting active holds (`POST /events/<id>/bookings/`) creates a `Booking` in `HELD` status and converts `SeatHold` records to `CONVERTED`.
- Each seat creates an immutable `BookingSeat` snapshot storing the exact `unit_price`, `category_id`, and `category_name` calculated at the moment of booking. Historical price snapshots cannot be tampered with or altered by future event price edits.

### 3. Payment Processing & Idempotency
- Customers initiate mock payment via `POST /events/bookings/<id>/start-payment/` supplying an `Idempotency-Key` header.
- Submitting the same idempotency key repeatedly returns the existing transaction result without double-charging or creating duplicate payments.
- When `PAYMENT_PROVIDER=mock_success`, booking moves from `PENDING_PAYMENT` to `CONFIRMED`.
- When `PAYMENT_PROVIDER=mock_failure`, booking remains `PENDING_PAYMENT`, allowing retries with a new key.

### 4. Cancellation & 24-Hour Refund Rule
- Customers can cancel their bookings via `POST /events/bookings/<id>/cancel/` from their booking detail page.
- **More than 24 hours prior to event start**: Cancellation marks booking `CANCELLED`, releases seats (`occupies_seat=False`), and automatically issues a full monetary refund linked to the original payment.
- **Within 24 hours of event start**: Cancellation marks booking `CANCELLED` and releases seats, but **no refund** is issued in accordance with the 24-hour cutoff policy.
- Repeat cancellation attempts are rejected with HTTP 409 Conflict.

### 5. In-App & Email Notifications
- Upon transaction commit, post-save signals dispatch notifications through customer channels.
- In-app notifications are visible at `/events/notifications/` with read/unread tracking and direct booking links.
- Email notifications are emitted via Django's email backend (console backend by default).

---

## Production Deployment & Hosting Guide

TicketFlow is packaged for deployment on PaaS providers such as **Render** or **Railway** using Gunicorn and WhiteNoise.

### Deployment on Render (Automated Blueprint)

1. Fork or push this repository to your GitHub account (`main` branch).
2. Log in to [Render](https://render.com/) and click **New → Blueprint**.
3. Select your repository. Render detects [`render.yaml`](file:///home/android5420/cPlusPlus/django/tickerflow/render.yaml) and automatically provisions:
   - A managed **PostgreSQL database** service (`ticketflow-db`).
   - A **Web Service** (`ticketflow-web`) running Gunicorn and WhiteNoise.
4. Set `ALLOW_MOCK_PAYMENTS_IN_PRODUCTION=True` in the web service environment settings for assignment demo evaluations.
5. Render runs the build command:
   ```bash
   pip install -r requirements.txt && python manage.py collectstatic --no-input && python manage.py migrate && python manage.py seed_demo_data
   ```
6. The application is immediately live with HTTPS, database connectivity, and seeded demo accounts.

### Manual / Generic PaaS Deployment (Procfile)

1. Set environment variables on your host:
   - `DJANGO_ENV=production`
   - `DJANGO_DEBUG=False`
   - `DJANGO_SECRET_KEY=<your-secret-key>`
   - `DJANGO_ALLOWED_HOSTS=<your-domain.com>`
   - `DB_ENGINE=postgresql`
   - `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_SSLMODE=require`
   - `PAYMENT_PROVIDER=mock_success`
   - `ALLOW_MOCK_PAYMENTS_IN_PRODUCTION=True`
2. Start process:
   ```bash
   web: gunicorn config.wsgi:application
   ```

---

## Testing & Quality Checks

Run the automated test suite and sanity checks:

```bash
# Run Django system checks
python manage.py check

# Verify migration consistency
python manage.py makemigrations --check --dry-run

# Run full test suite with coverage
pytest --cov=events --cov=accounts --cov=health --cov-report=term-missing
```
