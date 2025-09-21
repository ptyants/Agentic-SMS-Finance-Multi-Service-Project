"""
gemini_planner.py
Planner module for the agent.

This module wraps Google's Gemini models to map user prompts into either a structured function
call (via JSON Schema) or a final free‑text answer.  If a function call is returned, the
agent server will invoke the corresponding tool (e.g. retrieving account summary or searching
services) and return the result to the user.
"""

import os
from typing import Dict, Any, Optional

import google.generativeai as genai
from dotenv import load_dotenv

from config import GEMINI_API_KEY

# Load environment variables early (required for API key)
load_dotenv()

API_KEY = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY must be set in the environment or .env file.")

genai.configure(api_key=API_KEY)

# Define the functions that the planner can call.
# Each function is described using JSON Schema.  See the Google Generative AI docs for details.
FUNCTION_DECLARATIONS = [
    {
        "name": "get_account_summary",
        "description": "Lấy bản tóm tắt an toàn của 1 tài khoản (server sẽ sanitize).",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "bank_name": {"type": "string", "description": "Tên ngân hàng, nếu biết"}
            },
            "required": ["account_id"]
        }
    },
    {
        "name": "search_services",
        "description": "Tìm kiếm dịch vụ ngân hàng (vay, thẻ, tiết kiệm...) theo từ khóa.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Từ khóa dịch vụ, ví dụ: 'vay mua nhà', 'thẻ tín dụng'"
                },
                "bank_name": {
                    "type": "string",
                    "description": "Tên ngân hàng cần tìm kiếm (mặc định 'mock')",
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "list_user_accounts",
        "description": "Liệt kê tất cả ngân hàng và tài khoản mà user đang có, không yêu cầu OTP.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone_num": {"type": "string", "description": "Số điện thoại hoặc user_id"}
            },
            "required": ["phone_num"]
        }
    }
]

TOOLS = [{"function_declarations": FUNCTION_DECLARATIONS}]

SYSTEM_INSTRUCTION = (
    "Bạn là planner (kiểu ReAct). Nhiệm vụ của bạn là ánh xạ intent của người dùng "
    "thành lệnh gọi hàm (function_call) khi có tool phù hợp.\n\n"
    "Quy tắc:\n"
    "- Nếu người dùng hỏi 'tôi có tài khoản nào ở ngân hàng X', 'danh sách tài khoản', "
    "'liệt kê account' → gọi list_user_accounts.\n"
    "- Nếu người dùng hỏi về số dư, số tiền, giao dịch → gọi get_account_summary.\n"
    "- Nếu người dùng hỏi về dịch vụ, sản phẩm, gói, lãi suất, thẻ, vay, gửi tiết kiệm → gọi search_services.\n"
    "- Khi search_services, hãy dùng toàn bộ câu hỏi của người dùng làm query.\n"
    "- Nếu user không ghi rõ ngân hàng, dùng bank_name mặc định (mock_bank).\n"
    "- Không được trả lời trực tiếp khi có tool, luôn function_call để lấy dữ liệu từ Tool."
)

# Generation configuration for Gemini
GEN_CFG = {
    "temperature": 0.2,
    "top_p": 0.9,
    "top_k": 32,
}

TOOL_CFG = {
    "function_calling_config": {"mode": "ANY"}
}

def _build_model():
    """Try to instantiate gemini-2.5-pro, fall back to gemini-2.0-flash if not available."""
    try:
        return genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=SYSTEM_INSTRUCTION,
            tools=TOOLS,
            generation_config=GEN_CFG,
        )
    except Exception:
        return genai.GenerativeModel(
            model_name="gemini-2.5-pro",
            system_instruction=SYSTEM_INSTRUCTION,
            tools=TOOLS,
            generation_config=GEN_CFG,
        )


_model = _build_model()

def _extract_function_call(resp) -> Optional[Dict[str, Any]]:
    """Extract the first function call from a Gemini response if present."""
    try:
        for cand in getattr(resp, "candidates", []) or []:
            parts = getattr(getattr(cand, "content", None), "parts", []) or []
            for p in parts:
                fc = getattr(p, "function_call", None) or getattr(p, "functionCall", None)
                if fc and getattr(fc, "name", None):
                    args = dict(getattr(fc, "args", {}) or {})
                    return {"name": fc.name, "arguments": args}
    except Exception:
        pass
    return None

def call_gemini_planner(user_prompt: str) -> Dict[str, Any]:
    """
    Generate a plan for the given user prompt.  Returns a dict with:
      - {"type":"function_call", "name": ..., "arguments": {...}}
      - {"type":"final", "text": "..."}
    """
    try:
        resp = _model.generate_content(user_prompt, tool_config=TOOL_CFG)
        fc = _extract_function_call(resp)
        if fc:
            return {"type": "function_call", **fc}

        # fallback to plain text if no function call
        text = None
        try:
            text = resp.text
        except Exception:
            chunks = []
            for cand in getattr(resp, "candidates", []) or []:
                for p in getattr(getattr(cand, "content", None), "parts", []) or []:
                    if getattr(p, "text", None):
                        chunks.append(p.text)
            text = "".join(chunks).strip() if chunks else ""
        return {"type": "final", "text": text or ""}
    except Exception as e:
        return {"type": "final", "text": f"(planner lỗi) {str(e)}"}