# Owner Command Center guide

Owner controls (`/owner`, owners only) gained: **Overview**, **Monitoring**, **Limits**, **Maintenance**, **Owner actions**, plus a usage summary in each account dialog. Account search, suspend/restore, Special access, access codes, owners and the security log are the existing tabs.

**Privacy boundary.** Owner views show counts, sizes, states and timestamps only. They never include project names, file names, report titles or content, questions or answers. The owner role grants no access to anyone's work.

**Monitoring.** Each check is *Working*, *Degraded*, *No recent signal* or *Cannot be measured*. The overall state is the worst one; "cannot be measured" is never rounded up to healthy. CPU and memory are always *cannot be measured* (no agent is installed). Disk free space, load average, database reachability, running jobs with stale heartbeats (older than 15 minutes), overdue schedules (more than 30 minutes late: usually the scheduler script is not running) and permanently failed deliveries are reported.

**Limits.** Documents per standard account and pages per document. Changes need a recent password confirmation, apply to the next upload, and never delete anything. Special access lifts the limits. *Use default* removes an override.

**Maintenance mode.** Needs a recent password confirmation **and** an explicit confirm step. While on: non-owner accounts can sign in, read and sign out but every change returns `503 MAINTENANCE` with your notice; the scheduler pauses; owners are unaffected. Every on/off change is recorded with who and when in *Owner actions* and the audit log. Turn it off from the same tab.

**Auditing.** Block/unblock, Special access, quota and maintenance changes are all recorded (actor, action, target, outcome, time).
