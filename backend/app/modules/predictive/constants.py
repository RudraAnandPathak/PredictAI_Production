"""Predictive-modelling vocabulary: versions, problem types, job states, limits, audit events."""
from __future__ import annotations

ML_VERSION = "1"                         # bump when any modelling calculation changes; stored with every model
SCHEMA_ASSESSMENT = "predictai.ml-assessment/1"
SCHEMA_REPORT = "predictai.ml-report/1"
SCHEMA_PREDICTION = "predictai.ml-prediction/1"
SEED_DEFAULT = 20240601
MIN_ROWS = 30                            # fewer usable rows than this cannot be modelled or validated at all
SMALL_DATA_ROWS = 200                    # below this a model is usable but its score is very uncertain
MAX_CLASSES = 30
SVM_MAX_ROWS = 10_000                    # SVM training is roughly quadratic in rows: not offered beyond this
KNN_MAX_ROWS = 60_000
GB_MAX_CELLS = 3_000_000                 # classic gradient boosting is sequential and slow on very large tables


class Problem:
    BINARY = "binary_classification"
    MULTICLASS = "multiclass_classification"
    REGRESSION = "regression"
    CLASSIFICATION = (BINARY, MULTICLASS)
    ALL = (BINARY, MULTICLASS, REGRESSION)


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ACTIVE = (QUEUED, RUNNING)
    FINAL = (COMPLETED, FAILED, CANCELLED)
    ALL = (QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED)


class MEvent:
    ASSESSED = "ml.assessed"
    JOB_QUEUED = "ml.job.queued"
    JOB_STARTED = "ml.job.started"
    JOB_COMPLETED = "ml.job.completed"
    JOB_FAILED = "ml.job.failed"
    JOB_CANCELLED = "ml.job.cancelled"
    MODEL_DELETED = "ml.model.deleted"
    PREDICTED = "ml.predicted"
    BATCH_PREDICTED = "ml.batch_predicted"
    PREDICTION_DOWNLOADED = "ml.prediction.downloaded"
    EXPLAINED = "ml.explained"
    REJECTED = "ml.rejected"


# Resource limits: (standard account, account with compute.extended). Everything that can grow is bounded, and the bound is stated to the user.
LIMITS = {
    "max_rows": (100_000, 500_000),            # rows read from the stored table
    "max_train_rows": (20_000, 100_000),       # rows used to train; a larger table is sampled with a recorded seed
    "max_cells": (4_000_000, 20_000_000),
    "max_input_columns": (120, 400),
    "max_encoded_features": (400, 1500),
    "time_budget_s": (120, 600),
    "memory_mb": (1536, 4096),
    "max_hpo_trials": (30, 150),               # parameter sets evaluated per tuned algorithm
    "max_hpo_algorithms": (2, 4),
    "max_cv_folds": (5, 10),
    "max_cv_repeats": (2, 5),
    "max_models": (25, 250),
    "max_active_jobs": (1, 2),
    "max_model_bytes": (64 * 1024 * 1024, 256 * 1024 * 1024),
    "max_batch_rows": (5_000, 100_000),
    "max_result_bytes": 16 * 1024 * 1024,
}
MAX_PREDICTION_HISTORY = 200
MAX_SINGLE_VALUE_LEN = 500
BACKGROUND_ROWS = 300                          # feature rows kept beside a model for partial dependence and what-if explanations
CURVE_POINTS = 60
SCATTER_POINTS = 400
