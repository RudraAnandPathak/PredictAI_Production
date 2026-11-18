"""The PredictAI Brain: one place that sets up, guards and calls the model provider every AI feature shares (Google Gemini through AI Studio, with Ollama and the built-in rules behind it)."""
from .service import Answer, BrainService

__all__ = ["Answer", "BrainService"]
