# AI Usage

AI assistance was used during TicketFlow development for code implementation,
debugging, test creation, and documentation. The tools used were ChatGPT and
GitHub Copilot.

Examples from the development work include:

- Investigating the PostgreSQL error `FOR UPDATE cannot be applied to the
  nullable side of an outer join`. The relevant booking query selected the
  nullable seat-category relationship while locking holds; the query was
  narrowed to lock only the hold rows. The change was then checked with the
  pricing tests and the full local test suite. PostgreSQL-specific verification
  depends on running the tests against PostgreSQL.
- Using tests to verify booking lifecycle transitions, price snapshot behavior,
  payment idempotency, refund cutoffs, and notification ownership and delivery
  behavior.
- Using Django checks, migration consistency checks, and Git diff checks during
  implementation.

AI-generated suggestions and changes were reviewed and tested rather than
accepted automatically. No percentage of code authorship is claimed. The
developer remains responsible for understanding the implementation, validating
its behavior, explaining design decisions, and maintaining the project.
