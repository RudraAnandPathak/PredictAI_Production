"""PredictAI Research Intelligence (Phase 10): an independent subsystem for source-grounded research.

It does NOT import or depend on the Phase 6 research engine (app.modules.research). It reuses only platform-level services: identity (Principal, audit, rate limiting), the database,
the Phase 8 research workspace tables, and the platform AI provider router (configuration, privacy classes, fallback, usage). See docs/phase10/ARCHITECTURE.md."""
