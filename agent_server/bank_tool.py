"""
bank_tool.py
Open Banking Hub client: đa người dùng, đa ngân hàng, đa tài khoản.
Quy tắc endpoint (hub 1 host, route theo bank_name):
  - GET  {OPEN_BANKING_HUB}/bank/{bank_name}/accounts/{phone}
  - POST {OPEN_BANKING_HUB}/bank/{bank_name}/balance
  - POST {OPEN_BANKING_HUB}/bank/{bank_name}/otp/verify
  - GET  {OPEN_BANKING_HUB}/bank/{bank_name}/services?query=...
"""

import time
import requests
from typing import Dict, Any, List, Optional
from config import RAG_SERVICE_URL
from rag_tool import resolve_bank_collection

# Hub base URL (string)
OPEN_BANKING_HUB = "http://localhost:4000"

# In-memory token store (demo)
# key = f"{bank}:{phone}:{account_id}"
TOKEN_STORE: Dict[str, Dict[str, Any]] = {}
PENDING_OTP: Dict[str, Dict[str, Any]] = {}


class NeedOTP(Exception):
    def __init__(self, message: str, phone: Optional[str] = None, bank_name: Optional[str] = None, account_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.phone = phone
        self.bank_name = bank_name
        self.account_id = account_id


def _key(bank: str, phone: str, account_id: Optional[str] = None) -> str:
    return f"{bank}:{phone}:{account_id or ''}"


def _now() -> float:
    return time.time()

def get_supported_banks() -> list[str]:
    url = f"{OPEN_BANKING_HUB}/health"
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    data = r.json()
    return data.get("banks", [])


def get_cached_token(phone: str, bank_name: str, account_id: str) -> Optional[str]:
    key = _key(bank_name, phone, account_id)
    rec = TOKEN_STORE.get(key)
    if rec and rec.get("expires", 0) > _now():
        return rec["token"]
    if rec:
        del TOKEN_STORE[key]
    return None


def save_token(phone: str, bank_name: str, account_id: str, token: str, ttl_seconds: int = 600) -> None:
    key = _key(bank_name, phone, account_id)
    TOKEN_STORE[key] = {"token": token, "expires": _now() + ttl_seconds}


def request_otp_for_action(phone: str, bank_name: str, action: str = "get_account_summary", account_id: Optional[str] = None) -> Dict[str, Any]:
    """Yêu cầu OTP tại Hub (đa ngân hàng)."""
    if not OPEN_BANKING_HUB:
        raise ValueError("OPEN_BANKING_HUB chưa cấu hình")
    if not account_id:
        raise ValueError("account_id là bắt buộc với flow OTP demo này")

    payload = {"phone": phone, "account_id": account_id, "action": action}
    url = f"{OPEN_BANKING_HUB}/bank/{bank_name}/balance"
    r = requests.post(url, json=payload, timeout=10)
    if r.status_code < 400:
        PENDING_OTP[_key(bank_name, phone, account_id)] = {
            "action": action, "account_id": account_id, "created": _now()
        }
        return r.json()
    raise RuntimeError(f"Không gọi được request_otp: {r.text}")


def verify_otp_and_get_token(phone: str, otp: str, bank_name: str, account_id: str) -> Dict[str, Any]:
    url = f"{OPEN_BANKING_HUB}/bank/{bank_name}/otp/verify"
    payload = {"phone": phone, "otp": otp, "account_id": account_id}
    r = requests.post(url, json=payload, timeout=10)
    data = r.json()
    token = data.get("access_token")
    if token:
        save_token(phone, bank_name, account_id, token, ttl_seconds=int(data.get("ttl", 600)))
    PENDING_OTP.pop(_key(bank_name, phone, account_id), None)

    account_summary = data.get("account_summary")
    if account_summary:
        account_summary = sanitize_bank_response(account_summary)   # ✅ chuẩn hóa lại

    return {"token": token, "account_summary": account_summary}


def get_accounts(phone_num: str, bank_name: str) -> List[Dict[str, Any]]:
    """Lấy danh sách account theo user + bank."""
    url = f"{OPEN_BANKING_HUB}/bank/{bank_name}/accounts/{phone_num}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json() or []

def list_user_accounts(phone_num: str) -> Dict[str, Any]:
    banks = get_supported_banks()
    result = {}
    for bank in banks:
        try:
            accounts = get_accounts(phone_num, bank)
            if accounts:
                result[bank] = [
                    {"accountId": a["accountId"], "label": a.get("label"), "last_update": a.get("last_update")}
                    for a in accounts
                ]
        except Exception:
            continue
    return result



def get_account_summary(account_id: str, phone_num: str, bank_name: str = "mock") -> Dict[str, Any]:
    """
    Nếu chưa có token theo (bank, phone, account_id) -> gửi OTP (NeedOTP).
    Nếu có token -> (demo) trả về số dư/tx giả lập hoặc gọi thêm endpoint khác nếu bạn bổ sung.
    """
    if not OPEN_BANKING_HUB:
        raise ValueError("OPEN_BANKING_HUB chưa cấu hình")

    # 1) Token theo từng account
    token = get_cached_token(phone_num, bank_name, account_id)
    if not token:
        request_otp_for_action(phone_num, bank_name, action="get_account_summary", account_id=account_id)
        raise NeedOTP(
            f"OTP đã được gửi tới {phone_num} bởi {bank_name}",
            phone=phone_num, bank_name=bank_name, account_id=account_id
        )

    # 2) Demo: khi có token rồi, đọc lại thông tin account từ users.json để trả về (giả lập)
    acc_list = get_accounts(phone_num, bank_name)
    acc = next((a for a in acc_list if a.get("accountId") == account_id), None)
    if not acc:
        # fallback: lấy account đầu tiên
        acc = acc_list[0] if acc_list else {"accountId": account_id, "balance": 0, "label": "Unknown"}

    raw = {
        "account_number": acc.get("accountId", account_id),
        "balance": acc.get("balance", 0),
        "transactions": acc.get("transactions", []),
        "last_update": acc.get("last_update")
    }
    return sanitize_bank_response(raw)


# --- sanitize helpers ---

def mask_account(acc: str) -> str:
    acc = (acc or "").strip()
    if len(acc) <= 8:
        return "****"
    return acc[:4] + "..." + acc[-4:]


def summarize_transactions(txs: List[Dict[str, Any]], n: int = 5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in txs[:n]:
        merchant = t.get("merchant") or t.get("merchant_name") or t.get("mo_ta") or "[masked]"
        merchant = merchant if len(merchant) <= 30 else merchant[:27] + "..."
        out.append({
            "date": t.get("date") or t.get("ngay"),
            "amount": t.get("amount") or t.get("so_tien_vnd"),
            "merchant": merchant,
            "type": t.get("type") or t.get("danh_muc")
        })
    return out


def sanitize_bank_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "account_label": mask_account(str(raw.get("account_number", "[unknown]"))),
        "balance": raw.get("balance"),
        "recent_transactions": summarize_transactions(raw.get("transactions", []), n=5),
        "last_update": raw.get("last_update")
    }


def search_services(query: str, bank_name: str = "mock_bank") -> str:
    col_name = resolve_bank_collection(bank_name)
    if not col_name:
        return f"❌ Không tìm thấy collection cho ngân hàng {bank_name}"

    try:
        payload = {"bank_name": col_name, "query": query, "k": 5}
        url = f"{RAG_SERVICE_URL}/rag/search"

        print("📡 Request chuẩn bị gửi:")
        print("   URL:", url)
        print("   Body:", payload)

        resp = requests.post(url, json=payload, timeout=10)
        print("   Status:", resp.status_code)

        resp.raise_for_status()
        hits = resp.json().get("results", [])
        print("📩 Response JSON:", hits)

        if hits:
            lines = ["Các dịch vụ gợi ý:"]
            for h in hits:
                lines.append(f"- {h.get('text')}")
            return "\n".join(lines)
    except Exception as e:
        print("Search error:", e)
        return "❌ Lỗi khi tìm kiếm dịch vụ"

    return "Không tìm thấy dịch vụ phù hợp"
