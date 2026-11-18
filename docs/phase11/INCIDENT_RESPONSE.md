# Incident response

Runbooks for each category are in `RUNBOOKS.md`. This page is the process and the record template.

## Severity
| Level | Meaning | Examples | Target (suggestion) |
|---|---|---|---|
| **SEV1** | Data exposed to the wrong person, data lost or corrupted, or the service is down for everyone | cross-user leak, failed restore with no good backup | act at once; enable maintenance mode if writes may do harm |
| **SEV2** | A major capability is down or degraded for many people | training or exports failing for all, queue stuck | same day |
| **SEV3** | One capability degraded, workaround exists | one provider down (fallbacks active) | next working day |
| **SEV4** | Cosmetic or single-user | | backlog |

## Categories covered (brief §8.1 → runbook)
Authentication failure → 12 · Unauthorized access → 12 · Data leakage → 12 · Data corruption → 2, 11 · Failed backup → 10 · Failed restore → 11 · Database failure → 2 · Storage failure → 3 · Worker failure → 5 · Queue failure → 4 · Research-provider outage → 7 · AI-provider outage → 6 · Model execution failure → 9 · Report export failure → 8 · Resource exhaustion → 1, 3, 4 · Development sandbox failure → 13 · Dependency vulnerability → 12 (patch path) and `SECURITY_REVIEW.md`.

## Incident record (copy per incident)
```
Incident ID:            INC-YYYYMMDD-NN
Severity:               SEV_
Detected (UTC) / how:
Affected component:
Affected operations:
Current status:         investigating | mitigated | recovering | resolved | closed
Responder:
Safe diagnostics:       request IDs, error codes, counts, timestamps, versions. NEVER credentials, tokens, session cookies, document text or user data.
Mitigation:             (e.g. maintenance mode on at HH:MM)
Recovery:
Verification:           which checks passed (health/ready, owner monitoring, the affected workflow)
Closure (UTC):
Follow-up actions:      owner, due date, ticket
```
Store records somewhere access-controlled. **Do not paste secrets or content into records or chat.** Rotate any credential that was exposed, whatever the cause.

## Tools you actually have
Maintenance mode (owner, recent sign-in + confirmation), owner snapshot and audit events, request IDs in logs, `python -m app.cli` (migrate, list-owners, recover-owner, purge, oauth-status), `python -m app.ops.backup`, `python -m app.ops.release compat`. There is **no pager, no automatic alerting and no log shipping** — detection depends on the external checks in `MONITORING.md` or on users reporting.
