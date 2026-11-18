"""Analytics vocabulary: versions, limits, evidence status, audit events."""
from __future__ import annotations

CALC_VERSION = "1"                      # bump when any calculation changes; it is part of every result and evidence record
PLAN_SCHEMA = "predictai.analysis-plan/1"
EVIDENCE_SCHEMA = "predictai.evidence/1"
RESULT_SCHEMA = "predictai.analysis-result/1"

P_VALUE_NOTE = ("A p-value is the probability of seeing data at least this extreme if the null hypothesis were true. "
                "It is not the probability that the hypothesis is true, and it says nothing about how large or important an effect is.")
CORRELATION_NOTE = "An association in observed data. It does not show that one variable causes the other."
NOT_ENOUGH_EVIDENCE = "Not enough evidence."


class Evidence:
    VERIFIED = "VERIFIED"
    INCOMPLETE = "INCOMPLETE"


class RunKind:
    DASHBOARD = "dashboard"
    DESCRIPTIVE = "descriptive"
    RELATIONSHIPS = "relationships"
    COMPARE = "compare"
    HYPOTHESIS = "hypothesis"
    INSIGHTS = "insights"
    CHART = "chart"
    QUESTION = "question"
    ALL = (DASHBOARD, DESCRIPTIVE, RELATIONSHIPS, COMPARE, HYPOTHESIS, INSIGHTS, CHART, QUESTION)


class InsightState:
    NEW = "new"
    SAVED = "saved"
    DISMISSED = "dismissed"
    ALL = (NEW, SAVED, DISMISSED)


class AEvent:
    RUN = "analysis.run"
    RERUN = "analysis.rerun"
    EXPORTED = "analysis.exported"
    DRILLED = "analysis.drilled"
    INSIGHTS = "insights.generated"
    INSIGHT_STATE = "insight.state_changed"
    ASKED = "assistant.asked"
    REJECTED = "analysis.rejected"


# ---- resource limits (Standard accounts; accounts with compute.extended get the second column)
LIMITS = {
    "max_rows": (200_000, 1_000_000),
    "max_cells": (4_000_000, 20_000_000),
    "time_budget_s": (20, 90),
    "memory_mb": (1536, 4096),
    "max_operations": 40,
    "max_filters": 20,
    "max_columns_per_op": 60,
    "max_result_bytes": 8 * 1024 * 1024,
}
MAX_DRILL_ROWS = 200
MAX_EXPORT_ROWS = 100_000
MAX_SCATTER_POINTS = 5_000
MAX_CATEGORIES = 30
MAX_GROUPS = 12
MIN_N_CORR = 10
MIN_N_GROUP = 5
SEED_DEFAULT = 20240601
