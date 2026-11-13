# ScholarShip working instructions

- This is an independent project. Do not modify sibling projects or stop their services. Use the `scholarship` Compose project and explicit project directory via `bootstrap.compose_args`.
- The user confirms development stages. M0 initialization is authorized and implemented; consult `docs/initialization-report.md` and the latest user message before moving into a new business increment. A pending document never overrides authorization given later by the user.
- Keep normal runtime configuration editable near the top of Python entry scripts. CLI flags are optional overrides. Use this project's `.venv`, Node and pnpm, not global package upgrades.
- Read `docs/runbook.md` before running services. Local and container application modes are mutually exclusive. `stop.py` only stops verified local project launchers; it retains infrastructure.
- Keep `.env`, credentials, original PDFs, runtime files, logs, caches and dependencies out of Git. Never print credential values or full connection strings. The original competition materials directory is read-only.
- PostgreSQL is authoritative; tenant transactions must set `app.owner_id` locally. Runtime/auth/checkpoint roles have separate purposes. Only explicit migration tooling reads administrator credentials.
- Treat the initial Alembic implementation and catalog snapshots as frozen. Add migrations for subsequent changes; don't edit a deployed migration to change existing databases.
- Business modules are pending. Do not portray a local infrastructure probe, synthetic vectors, empty UI or configured credentials as a successful research result. Paid external services are disabled at M0.
- Run `validate.py` for meaningful foundation changes. It owns a unique temporary test database and isolated queue/collection; never clean unrelated resources. For process lifecycle changes, also use `runtime_smoke.py` with existing local launchers stopped.
