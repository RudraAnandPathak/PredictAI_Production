# Compute, quotas and limits

Defaults come from `.env` (`PREDICTAI_DEV_*`, see `config/env.example`); only the platform owner can override a limit per user or project (`PUT /dev/owner/compute-overrides`: Owner permission, fresh authentication, a written reason, audited, clearable).

| Limit | Default | Notes |
|---|---|---|
| Time per job | 300 s | per-job requests can only lower it |
| Processor time per job | 300 s | kernel `RLIMIT_CPU` |
| Memory per job | 512 MB | monitored; process group killed (`memory_limit`) |
| Output per job | 1024 KB | truncated and stopped |
| Processes per job | 32 | monitored |
| Files written per job | 200 MB | |
| Running jobs per user / project | 1 / 1 | |
| Queue depth | 20 | |
| Project storage | 200 MB | files + artifacts; `STORAGE_QUOTA_EXCEEDED` |
| Processor seconds per day per user | 3600 | |
| Sessions at once | 2 | |

The Compute page shows measured usage against each limit, warns at 80%, and lists **capabilities**. Pause, resize and GPU are reported **unavailable** with reasons: the local provider cannot checkpoint a job, limits are fixed per job, and no GPU provider exists (PredictAI does not rent or advertise managed GPUs). **Any service that could create charges needs explicit owner approval; none is used.**
