# AI Usage

## 1. AI Tools Used

During the development, testing, and documentation of TicketFlow, the following AI tools were utilized:
- **Antigravity AI Agent** (Google DeepMind)
- **GitHub Copilot** (GitHub / OpenAI)
- **ChatGPT** (OpenAI)

---

## 2. What AI Contributed

AI tools were leveraged as engineering assistants for scaffolding, code generation, and test authoring:

- **Architectural Scaffolding**: Prototyped the Service Layer pattern separation (`services.py`, `booking_services.py`, `payment_services.py`, `refund_services.py`) and the mock payment provider abstraction (`payment_providers.py`).
- **User Interface & Templates**: Drafted modern, responsive Bootstrap 5 templates extending `base.html`, including the event discovery grid, interactive SVG seat map, booking details, customer notification center, and organizer management portal.
- **Automated Test Generation**: Authored comprehensive test suites covering seat holding, booking conversions, payment idempotency, 24-hour cancellation refunds, organizer authorization boundaries, and idempotent demo data seeding.
- **DevOps & Documentation**: Generated initial GitHub Actions CI/CD workflow configurations (`ci.yml`), Render deployment blueprints (`render.yaml`), `Procfile`, and system architecture diagrams.

---

## 3. What the Developer Reviewed, Tested, and Fixed

All AI suggestions and generated code were rigorously reviewed, independently tested, and corrected before acceptance. Key areas of developer oversight and debugging include:

1. **PostgreSQL Concurrency & Outer Join Locking**:
   - *Issue*: PostgreSQL raised `FOR UPDATE cannot be applied to the nullable side of an outer join` when querying holds joined with nullable seat categories.
   - *Fix*: Investigated SQL query plans and restricted row locking specifically to hold records using `select_for_update(of=("self",))`. Validated behavior against live PostgreSQL instances in CI.
2. **Database Model Integrity & Relational Constraints**:
   - *Issue*: AI-generated tests attempted to instantiate `BookingSeat` without linking an active or converted `source_hold`.
   - *Fix*: Enforced compliance with the database schema by properly creating a `SeatHold` in `CONVERTED` status and linking `source_hold=hold` on every `BookingSeat` record.
3. **Choice Constant & Model Enum Alignments**:
   - *Issue*: Inconsistencies arose where generated code checked `Payment.Status.SUCCESS` instead of the actual model definition `Payment.Status.SUCCEEDED = "succeeded"`.
   - *Fix*: Audited all payment and refund references across services, templates, and tests to ensure 100% adherence to `Payment.Status` and `Refund.Status` choices.
4. **HTML Test Assertion Refinement**:
   - *Issue*: Substring assertions such as `content.count("seat-btn")` mistakenly matched CSS class definitions inside `<style>` blocks rather than rendered button elements.
   - *Fix*: Re-engineered test assertions to verify exact rendered HTML attributes (`class="seat-btn seat-available"`).
5. **Dual HTML / DRF API Routing**:
   - *Issue*: Views initially returned only JSON or only HTML, breaking either the interactive browser workflow or automated REST test clients.
   - *Fix*: Implemented header-based content negotiation in `booking_views.py` and `notification_views.py` checking `request.headers.get("Accept", "").startswith("text/html")` to return user-friendly Bootstrap templates for browser users while maintaining clean JSON responses for REST clients.
6. **Full Test Suite & Migration Validation**:
   - Independently ran the complete test suite (184+ tests), verified migration idempotency (`makemigrations --check --dry-run`), executed Django system checks, and verified that demo seeding runs idempotently without creating duplicate records.

---

## 4. Responsibility Statement

AI was used as an assistive tool to accelerate development. The developer reviewed all generated code, tested edge cases, verified security and concurrency safeguards, and maintains full understanding and ownership of the system architecture and implementation.
