"""The Gemini preset. Facts here come from Google's documentation (OpenAI-compatible endpoint, Bearer key, quota resetting at midnight Pacific time, limits that vary by model and are shown
in the AI Studio rate-limit console). Nothing is assumed about which model names exist for a given key: the owner chooses the model and PredictAI checks it against the list the key can see."""
from __future__ import annotations

from typing import Any, Dict

GEMINI_KEY = "gemini"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_ENV = "PREDICTAI_AI_KEY_GEMINI"
GEMINI_QUOTA_TZ = "America/Los_Angeles"

# Conservative starting caps. Google publishes the real per-model limits only in the AI Studio rate-limit console for your own project, and they differ by model; the owner should replace
# these with the numbers shown there. A cap that is too low only makes PredictAI fall back sooner; a cap that is too high lets Google refuse requests.
DEFAULT_DAILY_REQUESTS = 15
DEFAULT_PER_MINUTE = 4

GEMINI_TASKS = ["plan", "summarize", "synthesize", "extract", "classify"]

FREE_TIER_NOTICE = ("On Google's free tier, the text PredictAI sends may be used by Google to improve its products, and people may read it. Your papers, notes and notebook sources are sent unless you switch off "
                    "'allow external AI' in your own settings (that switch affects only you). Passwords, keys and access codes are never sent.")

COST_NOTICE = ("PredictAI marks this provider as free and never switches billing on. It cannot know whether the Google project behind your key has billing enabled: if it does, requests above the free "
               "limits are charged by Google. The request caps below are the guard; keep them at or below the numbers shown in your AI Studio rate-limit console.")


def gemini_fields(model: str, daily_request_limit: int, requests_per_minute: int) -> Dict[str, Any]:
    return {"label": "Google Gemini (AI Studio)", "base_url": GEMINI_BASE_URL, "model": model, "secret_env": GEMINI_ENV, "tasks": list(GEMINI_TASKS), "priority": 20, "timeout_s": 90, "max_retries": 1,
            "daily_request_limit": daily_request_limit, "requests_per_minute": requests_per_minute, "quota_tz": GEMINI_QUOTA_TZ,
            "notes": "Set up by the PredictAI Brain. Free tier: see the data notice."}
