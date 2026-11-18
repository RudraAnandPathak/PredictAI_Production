"""Identity and access management.

Layers (each only depends on the ones above it in this list):
  constants / errors / validation / passwords / tokens   pure building blocks
  repository                                             SQL only, no business rules
  audit / ratelimit / email                              cross-cutting services
  authorization                                          roles, permissions, principals
  authentication / users / access_codes / oauth          use cases
  provisioning                                           owner bootstrap and recovery (CLI)
  container                                              wires the services for one request
Nothing here imports FastAPI; app.api is a thin HTTP adapter over these services.
"""
