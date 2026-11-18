"""Domain modules (modular monolith).

Each future domain gets its own subpackage with an explicit service interface and
independent tests: identity, projects/entitlements, documents/data, analytics,
predictive, exam, research/world, reports/collaboration, development/jobs, owner,
audit/usage. Routers in app.api call these services; modules never import routers.
No domain is implemented in Phase 1.
"""
