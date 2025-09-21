"""
memory_manager.py
Chat history management via Redis.

Each user has a separate Redis key storing a LangChain `RedisChatMessageHistory`.  Sessions
expire automatically after a configurable TTL.  This module hides the details of connecting
to Redis and creating new sessions on demand.
"""

import redis
from langchain_community.chat_message_histories import RedisChatMessageHistory
from dotenv import load_dotenv
import os

from config import REDIS_HOST, REDIS_PORT, REDIS_PASS

# Ensure environment variables are loaded
load_dotenv()

# Construct Redis URL: include password if provided
if REDIS_PASS:
    # REDIS_URL = f"redis://default:{REDIS_PASS}@{REDIS_HOST}:{REDIS_PORT}"
    REDIS_URL = f"redis://default:{REDIS_PASS}@redis-13812.crce194.ap-seast-1-1.ec2.redns.redis-cloud.com:13812"
else:
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}"

# Instantiate a root redis client (decode_responses ensures we get str values)
r = redis.from_url(REDIS_URL, decode_responses=True)


def ensure_session(user_id: str, ttl: int = 86400) -> None:
    """
    Ensure a chat session exists for the given user ID.  If the session does not yet exist,
    a new `RedisChatMessageHistory` is created with the provided TTL (default: 24 hours).
    """
    key = f"message_store:{user_id}"
    if not r.exists(key):
        RedisChatMessageHistory(session_id=user_id, url=REDIS_URL, ttl=ttl)
        print(f"✅ Created new session for {user_id}")
    else:
        print(f"ℹ️ Session for {user_id} already exists.")


def get_chat_history(user_id: str, ttl: int = 86400) -> RedisChatMessageHistory:
    """Retrieve the chat history object for a given user.  Ensures the session exists first."""
    ensure_session(user_id, ttl=ttl)
    return RedisChatMessageHistory(session_id=user_id, url=REDIS_URL, ttl=ttl)