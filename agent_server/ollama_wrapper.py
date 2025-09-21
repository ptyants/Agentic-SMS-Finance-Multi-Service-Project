"""
ollama_wrapper.py
Simple wrapper around a local LLaMA (served by Ollama) for paraphrasing responses.

The agent uses Gemini as a planner to decide which tools to call and what information to return.
Once tool data and/or Gemini text are available, this wrapper helps shape a final natural
language reply.  The wrapper makes a blocking HTTP call to the Ollama API and returns
the generated text.
"""

from typing import Optional, Mapping, Any, List
import requests
from pydantic import BaseModel

from config import OLLAMA_URL, OLLAMA_MODEL


class OllamaConfig(BaseModel):
    url: str = OLLAMA_URL
    model: str = OLLAMA_MODEL


class OllamaLLM:
    """Very thin synchronous wrapper around the Ollama HTTP API."""

    def __init__(self, config: Optional[OllamaConfig] = None) -> None:
        self.config = config or OllamaConfig()

    def invoke(self, prompt: str, stop: Optional[List[str]] = None) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "prompt": prompt,
            "options": {
                "temperature": 0.2,
                "num_predict": 256
            },
            "stream": False
        }
        if stop:
            payload["stop"] = stop
        resp = requests.post(self.config.url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "").strip()

    @property
    def _identifying_params(self) -> Mapping[str, Any]:
        return {"model": self.config.model, "url": self.config.url}

    @property
    def _llm_type(self) -> str:
        return "ollama"