"""
service.py
Core service logic for the agent.

This module exposes `handle_ask` which orchestrates the planner (Gemini), tool calls
(`bank_tool.get_account_summary`, `bank_tool.search_services`, `bank_tool.list_user_accounts`)
and the final response synthesis via a local LLM (Ollama). It maintains a global
`PENDING_ACTIONS` map to resume flows that require OTP verification.
"""

from fastapi import HTTPException
import uuid
import requests

from config import SERVICE_TOKEN
from gemini_planner import call_gemini_planner
from bank_tool import (
    get_account_summary,
    get_supported_banks,
    list_user_accounts,
    search_services,
    verify_otp_and_get_token,
    NeedOTP
)
from ollama_wrapper import OllamaLLM
from memory_manager import get_chat_history


llm = OllamaLLM()

# Global registry of pending actions keyed by user_id
PENDING_ACTIONS: dict[str, dict[str, str]] = {}


def synthesize_reply(user_prompt: str, intent: str, tool_data=None, gemini_text=None, context: str = "") -> str:
    """
    Use the local LLM to generate a final answer given the raw tool data and/or planner text.
    The system prompt enforces tone (natural, friendly, concise, Vietnamese only) and
    instructs the LLM to prefer tool data when available.
    """
    system_prompt = (
        "Bạn là trợ lý liên ngân hàng của tập đoàn sovico thân thiện. "
        "Nhiệm vụ của bạn là kết nối các thông tin liên quan theo yêu cầu và trả lời khách hàng bằng tiếng Việt, ngắn gọn nhưng đầy đủ. "
        "Quy tắc:\n"
        "- Ưu tiên sử dụng dữ liệu từ Tool (số dư, dịch vụ, giao dịch...).\n"
        "- Nếu dữ liệu từ Tool thiếu hoặc trống, hãy tổng hợp từ Gemini và kiến thức nền.\n"
        "- Nếu vẫn thiếu, hãy đưa ra danh sách dịch vụ cơ bản (thẻ tín dụng, chuyển khoản, gửi tiết kiệm, vay tiêu dùng, tra cứu số dư...).\n"
        "- Không được trả lời chung chung kiểu 'ngân hàng có nhiều dịch vụ'. Luôn đưa ví dụ hoặc danh sách cụ thể.\n"
        "- Giữ văn phong tự nhiên, rõ ràng, súc tích, thân thiện như đang trò chuyện trực tiếp."
    )

    # Compose a human-readable representation of tool_data
    tool_text = ""
    if tool_data:
        if intent == "get_account_summary":
            tool_text = (
                f"Tài khoản: {tool_data['account_label']}\n"
                f"Số dư: {tool_data['balance']}\n"
                f"Giao dịch gần đây:\n"
            )
            for t in tool_data.get("recent_transactions", []):
                tool_text += f"- {t['date']}: {t['amount']} ({t['merchant']})\n"
            if tool_data.get("last_update"):
                tool_text += f"Cập nhật: {tool_data['last_update']}"
        elif intent == "list_user_accounts":
            tool_text = "Danh sách tài khoản của bạn:\n"
            for bank, accounts in tool_data.items():
                tool_text += f"- {bank}:\n"
                for a in accounts:
                    tool_text += f"   • {a['label']} (ID: {a['accountId']})\n"
        else:
            tool_text = str(tool_data)

    # Build prompt for Ollama
    prompt_for_llama = (
        f"<<SYS>>{system_prompt}<<SYS>>\n\n"
        f"--- Lịch sử hội thoại ---\n{context}\n\n"
        f"--- Người dùng hỏi ---\n{user_prompt}\n\n"
        f"--- Tool data ---\n{tool_text}\n\n"
        f"--- Gemini text ---\n{gemini_text or ''}\n\n"
        f"Hãy tạo câu trả lời thân thiện cho khách hàng."
    )
    return llm.invoke(prompt_for_llama)


def handle_ask(user_id: str, prompt: str, account_id: str | None = None,
               bank_name: str = "mock", phone_num: str | None = None) -> dict:
    """
    Main entry point for processing a user prompt. Decides whether to resume an OTP
    flow, call the planner, invoke tools, or return a simple chat reply. Returns a
    dictionary with a `reply` field containing the text answer.
    """
    banks = get_supported_banks()
    if bank_name not in banks:
        msg = f"❌ Hệ thống chưa hỗ trợ ngân hàng {bank_name}. Vui lòng chọn trong: {', '.join(banks)}"
        return {"reply": msg, "source": "unsupported_bank"}

    trace_id = str(uuid.uuid4())
    print(f"[TRACE:{trace_id}] user_id={user_id}, ask={prompt}")
    chat_history = get_chat_history(user_id)
    past_messages = chat_history.messages
    context = "\n".join([f"{m.type}: {m.content}" for m in past_messages])

    # Nếu user nhập OTP
    if prompt.strip().isdigit() and len(prompt.strip()) in (5, 6):
        pending = PENDING_ACTIONS.get(user_id)
        if pending:
            phone = pending["phone"]
            bank = pending["bank_name"]
            acct = pending.get("account_id")
            if not acct:
                return {"reply": "❌ Thiếu account_id khi xác minh OTP", "source": "otp_failed"}
            try:
                res = verify_otp_and_get_token(phone, prompt.strip(), bank, acct)
                account_summary = res.get("account_summary")
                if account_summary:
                    ans = synthesize_reply(
                        "Xác thực OTP và trả số dư",
                        "get_account_summary",
                        tool_data=account_summary,
                        context=context
                    )
                else:
                    safe = get_account_summary(account_id=acct, phone_num=phone, bank_name=bank)
                    ans = synthesize_reply(
                        "Xác thực OTP và trả số dư",
                        "get_account_summary",
                        tool_data=safe,
                        context=context
                    )
                PENDING_ACTIONS.pop(user_id, None)
                chat_history.add_user_message(prompt)
                chat_history.add_ai_message(ans)
                return {"reply": ans, "source": "otp_verified_resume"}
            except Exception as e:
                err_msg = f"Xác thực OTP thất bại: {str(e)}"
                chat_history.add_user_message(prompt)
                chat_history.add_ai_message(err_msg)
                return {"reply": err_msg, "source": "otp_failed"}

    # Nếu user chỉ hỏi "tài khoản" (không có số dư/giao dịch) → liệt kê accounts
    if "tài khoản" in prompt.lower() and "số dư" not in prompt.lower() and "giao dịch" not in prompt.lower():
        accounts_by_bank = list_user_accounts(phone_num)
        ans = synthesize_reply(prompt, "list_user_accounts", tool_data=accounts_by_bank, context=context)
        chat_history.add_user_message(prompt)
        chat_history.add_ai_message(ans)
        return {"reply": ans, "source": "AI_list_accounts_shortcut"}

    # Otherwise call the planner
    planner_resp = call_gemini_planner(f"Lịch sử hội thoại:\n{context}\n\nUser hỏi: {prompt}")
    intent_type = planner_resp.get("type")

    # Case 1: plain answer from planner
    if intent_type == "final":
        if "dịch vụ" in prompt.lower():
            services = search_services(query=prompt, bank_name=bank_name)
            ans = synthesize_reply(prompt, "search_services", tool_data=services, context=context)
            chat_history.add_user_message(prompt)
            chat_history.add_ai_message(ans)
            return {"reply": ans, "source": "llama_wrap_service"}
        ans = synthesize_reply(prompt, "chitchat", gemini_text=planner_resp.get("text"), context=context)
        chat_history.add_user_message(prompt)
        chat_history.add_ai_message(ans)
        return {"reply": ans, "source": "llama_wrap_final"}

    # Case 2: planner wants to call a function
    if intent_type == "function_call":
        fn = planner_resp["name"]

        if fn == "get_account_summary":
            account_id_from_planner = planner_resp["arguments"].get("account_id")
            acct = account_id or account_id_from_planner

            # auto resolve từ phone nếu chưa có
            if not acct and phone_num:
                try:
                    accounts = requests.get(
                        f"http://localhost:4000/bank/{bank_name}/accounts/{phone_num}",
                        timeout=10
                    ).json()
                    if accounts:
                        acct = accounts[0]["accountId"]
                except Exception as e:
                    print(f"[TRACE:{trace_id}] ⚠️ Error auto-resolve account_id: {e}")

            if not acct:
                clarification = "Bạn muốn kiểm tra số dư ở ngân hàng nào?"
                chat_history.add_user_message(prompt)
                chat_history.add_ai_message(clarification)
                return {"reply": clarification, "source": "clarification"}

            try:
                safe = get_account_summary(account_id=acct, phone_num=phone_num, bank_name=bank_name)
                ans = synthesize_reply(prompt, "get_account_summary", tool_data=safe, context=context)
                chat_history.add_user_message(prompt)
                chat_history.add_ai_message(ans)
                return {"reply": ans, "source": "AI_summary_Account"}
            except NeedOTP as n:
                acct_id = n.account_id or acct
                if not acct_id:
                    try:
                        accounts = requests.get(
                            f"http://localhost:4000/bank/{n.bank_name or bank_name}/accounts/{n.phone}",
                            timeout=10
                        ).json()
                        if accounts:
                            acct_id = accounts[0]["accountId"]
                    except Exception as e:
                        print(f"[TRACE:{trace_id}] ⚠️ Fallback account_id error: {e}")

                if not acct_id:
                    raise HTTPException(status_code=500, detail="Thiếu account_id khi yêu cầu OTP")

                PENDING_ACTIONS[user_id] = {
                    "phone": n.phone,
                    "bank_name": n.bank_name or bank_name,
                    "action": "get_account_summary",
                    "account_id": acct_id
                }
                msg = (
                    f"Ngân hàng {n.bank_name or bank_name} đã gửi mã OTP tới số {n.phone} "
                    f"cho tài khoản {acct_id}. Vui lòng nhập mã OTP để xác thực."
                )
                return {"reply": msg, "source": "need_otp"}

            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        elif fn == "list_user_accounts":
            phone = planner_resp["arguments"].get("phone_num") or phone_num
            if not phone:
                return {"reply": "❌ Thiếu phone_num để tra cứu tài khoản", "source": "AI_list_accounts_failed"}
            accounts_by_bank = list_user_accounts(phone)
            ans = synthesize_reply(prompt, "list_user_accounts", tool_data=accounts_by_bank, context=context)
            chat_history.add_user_message(prompt)
            chat_history.add_ai_message(ans)
            return {"reply": ans, "source": "AI_list_accounts"}

        elif fn == "search_services":
            query = planner_resp["arguments"].get("query")
            bank = planner_resp["arguments"].get("bank_name", bank_name)
            if not query:
                raise HTTPException(status_code=400, detail="query required")
            services = search_services(query=query, bank_name=bank)
            ans = synthesize_reply(prompt, "search_services", tool_data=services, context=context)
            chat_history.add_user_message(prompt)
            chat_history.add_ai_message(ans)
            return {"reply": ans, "source": "AI_service"}

        else:
            ans = synthesize_reply(prompt, "unsupported", gemini_text=f"⚠️ Function {fn} chưa hỗ trợ.", context=context)
            chat_history.add_user_message(prompt)
            chat_history.add_ai_message(ans)
            return {"reply": ans, "source": "AI_tools_unsupported"}

    raise HTTPException(status_code=500, detail="Unexpected planner response.")
