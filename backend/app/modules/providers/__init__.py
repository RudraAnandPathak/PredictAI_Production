"""AI provider routing: adapters, task-based selection, privacy and cost controls, usage tracking and output verification. Works with no provider configured."""
from .base import (ALL_TASKS, DATA_CLASSES, DEFAULT_TASKS, EMBED_TASKS, GENERATE_TASKS, KINDS, PRIVACY_CLASSES, Adapter, Completion, Consent, Embedded, ProviderError, RouteOutcome,
                   build_messages, neutralise)
from .router import AdapterFactory, BreakerBoard, Router
from .service import ProviderService, classify_base_url
from .store import ProviderStore
from .verify import check_generated

__all__ = ["ALL_TASKS", "DATA_CLASSES", "DEFAULT_TASKS", "EMBED_TASKS", "GENERATE_TASKS", "KINDS", "PRIVACY_CLASSES", "Adapter", "AdapterFactory", "BreakerBoard", "Completion", "Consent",
           "Embedded", "ProviderError", "ProviderService", "ProviderStore", "RouteOutcome", "Router", "build_messages", "check_generated", "classify_base_url", "neutralise"]
