# AI Usage and Developer Ownership

## 1. AI Tools Used

During the development of TicketFlow, I used the following AI-assisted development tools:

- **ChatGPT**
- **GitHub Copilot**
- **Antigravity AI Agent**

My goal was to use AI to improve development speed and reduce repetitive work while maintaining responsibility for the system's architecture, engineering decisions, and final implementation.

## 2. My Engineering Responsibilities

I treated AI as a development assistant, not as the decision-maker.

My responsibilities included:

- Defining the project requirements, scope, and core business rules.
- Deciding the major architectural direction and separation of responsibilities.
- Identifying the main application workflows, including event management, seat holding, booking, payment, and cancellation.
- Determining important system constraints, such as temporary seat holds, booking state transitions, payment idempotency, and concurrency safety.
- Reviewing proposed implementations and deciding whether they fit the project.
- Verifying correctness through code review, database testing, and automated tests.

I used AI to accelerate implementation, but I remained responsible for deciding what should be built and whether the result was correct.

## 3. Where AI Helped

AI tools were mainly used to accelerate implementation and repetitive engineering tasks.

### Code implementation and scaffolding

- Helped draft service-layer code and repetitive implementation structures.
- Assisted with implementing the mock payment provider abstraction.
- Helped generate and refine Django views, templates, and supporting code.

### User interface

- Assisted with Bootstrap 5 templates extending `base.html`.
- Helped implement event discovery pages, seat-map interfaces, booking pages, notification views, and organizer management screens.

### Testing

- Helped draft and expand automated tests for booking transitions, seat holds, payment idempotency, refunds, authorization, and demo-data seeding.
- Helped identify missing edge cases and improve test coverage.

### DevOps and documentation

- Assisted with initial GitHub Actions workflow configuration, Render deployment configuration, and project documentation.
- Helped explain CI errors and investigate deployment issues.

## 4. Examples of Developer Review and Debugging

AI-generated code and suggestions were not accepted blindly. I reviewed the implementation and used actual test results to identify and correct problems.

### Example 1: PostgreSQL concurrency and row locking

**Problem:** PostgreSQL raised:

`FOR UPDATE cannot be applied to the nullable side of an outer join`

The query involved related seat-category data, which introduced a nullable outer join.

**My correction:** I investigated the query and restricted row locking to the primary model using:

`select_for_update(of=("self",))`

I then validated the behavior through PostgreSQL CI testing and added a regression test.

### Example 2: Database relationship integrity

**Problem:** Some generated tests attempted to create `BookingSeat` records without satisfying the required relationship with an active or converted source hold.

**My correction:** I aligned the test setup with the actual database model relationships by creating the appropriate `SeatHold`