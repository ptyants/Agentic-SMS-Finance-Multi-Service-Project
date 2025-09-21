# config.py
import os
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

# Configuration variables used across the agent service

# Ollama settings: used for the local LLM that paraphrases final answers
# OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3:8b")

# Webhook verification token (must match the banking server)
SERVICE_TOKEN = os.getenv("SERVICE_TOKEN", "devtoken")

# Gemini API key for the planner (set via .env)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Redis credentials for chat history storage
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASS = os.getenv("REDIS_PASS", "")

# Default phone used by UI (virtual id, no real SIM needed)
DEFAULT_PHONE = os.getenv("DEFAULT_PHONE", "demo:thao")

MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = "19530"

# RAG service base URL (for searching banking services)
RAG_SERVICE_URL = "http://localhost:8002"