"""Universal "Explanation" mechanism. Importing this package registers every explainer."""
from . import analytics, predictive  # noqa: F401  (registers the analytics, insight, assistant, profile and predictive-modelling explainers)
from .core import SCHEMA, TITLE, Draft, Say, Unverifiable, explain, explainer, unavailable
from .analytics import attach_to_run, explain_op

__all__ = ["SCHEMA", "TITLE", "Draft", "Say", "Unverifiable", "explain", "explainer", "unavailable", "attach_to_run", "explain_op"]
