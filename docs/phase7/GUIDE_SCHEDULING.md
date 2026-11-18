# Scheduled reports guide

- Create from *Schedules* or from a report page: once, daily, weekly (weekday) or monthly (day of month; short months use their last day), at a local time in a time zone you choose.
- **Mode:** *Refresh* sends the report's latest saved version each run (it does not re-run research or calculations); *Snapshot* always sends a pinned version.
- **Delivery:** every run keeps the exported file in PredictAI (always available). Email and Slack are offered only when this server has them configured; otherwise the page says they are unavailable. Notices to email/Slack never contain report content, only a pointer.
- **Running:** nothing runs inside the web server. `backend/scripts/run_schedules.py` must be run regularly (see INSTALL). It runs due schedules and retries failed deliveries.
- **Reliability:** runs and deliveries have idempotency keys, so a repeated script run cannot send twice. A failed delivery is retried up to 3 times (after 1, 5 and 30 minutes), then marked *Failed for good*; you can retry it manually. Missed runs (server off) are not replayed.
- **Access is re-checked** at run time and for each delivery. If you lose access to the report the schedule becomes *Blocked*.
- **Maintenance mode** pauses the scheduler.
