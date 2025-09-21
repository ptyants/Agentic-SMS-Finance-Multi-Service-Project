# ui_agent.py
import streamlit as st
import requests
import os
import threading
import time
import json
import websocket
import queue

# Endpoints
AI_API = os.getenv("AI_API", "http://localhost:8000/ask")
BANK_API = os.getenv("BANK_API", "http://localhost:4000")
DEFAULT_PHONE = os.getenv("DEFAULT_PHONE", "demo:thao")
WS_URL = os.getenv("BANK_WS", "ws://localhost:4000/ws")

st.set_page_config(page_title="💬 Multi-Service Agent", layout="centered")
st.title("💬 Multi-Service Agent")

# ---------- State ----------
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []
if "sms_inbox" not in st.session_state:
    st.session_state["sms_inbox"] = []
if "ws_thread_running" not in st.session_state:
    st.session_state["ws_thread_running"] = False
if "current_phone" not in st.session_state:
    st.session_state["current_phone"] = DEFAULT_PHONE

# Queue trung gian giữa thread WS và Streamlit
if "ws_queue" not in st.session_state:
    st.session_state["ws_queue"] = queue.Queue()

# ---------- WS listener ----------
def ws_listener(phone: str, q: queue.Queue):
    url = f"{WS_URL}?phone={phone}"

    def on_message(ws, message):
        try:
            data = json.loads(message)
        except:
            data = {"event": "raw", "payload": message}
        q.put(data)  # ✅ chỉ push vào queue

    def on_error(ws, err):
        q.put({"event": "ws_error", "payload": str(err)})

    def on_close(ws, *_):
        q.put({"event": "ws_closed", "payload": f"closed for {phone}"})

    def on_open(ws):
        q.put({"event": "ws_open", "payload": f"connected for {phone}"})

    while True:
        try:
            ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open,
            )
            ws.run_forever()
        except Exception as e:
            q.put({"event": "ws_exception", "payload": str(e)})
        time.sleep(2)  # backoff

# UI controls for phone subscribe
with st.sidebar:
    st.subheader("📱 Cấu hình SMS WS")
    new_phone = st.text_input("Phone cho WS:", value=st.session_state["current_phone"])
    colA, colB = st.columns(2)
    if colA.button("🔌 Kết nối WS"):
        st.session_state["current_phone"] = new_phone.strip() or DEFAULT_PHONE
        if not st.session_state["ws_thread_running"]:
            t = threading.Thread(
                target=ws_listener,
                args=(st.session_state["current_phone"], st.session_state["ws_queue"]),
                daemon=True,
            )
            t.start()
            st.session_state["ws_thread_running"] = True
        st.success(f"Đang nghe SMS cho {st.session_state['current_phone']}")

# Tabs
tab1, tab2, tab3 = st.tabs(["🤖 AI Agent Chat", "🏦 Banking OTP", "📨 SMS Inbox"])

# --- Tab 1: AI Agent Chat ---
with tab1:
    st.subheader("📱 SMS Chat với AI Agent")
    phone_number = st.text_input("Số điện thoại (virtual):", value=DEFAULT_PHONE, key="ai_phone")
    user_input = st.text_input("Tin nhắn:", key="ai_msg")

    if st.button("📨 Gửi tin nhắn", key="ai_send"):
        if not user_input.strip():
            st.warning("⚠️ Nhập tin nhắn trước.")
        else:
            try:
                payload = {"phone_num": phone_number, "message": user_input}
                resp = requests.post(AI_API, json=payload, timeout=15)
                data = resp.json()
                reply = data.get("reply") or data.get("text") or str(data)
                st.session_state["chat_history"].append(("👤 Bạn", user_input))
                st.session_state["chat_history"].append(("🤖 Agent", reply))
            except Exception as e:
                st.error(f"❌ Lỗi khi gửi API: {e}")

    st.subheader("📜 Lịch sử trò chuyện")
    for role, msg in st.session_state["chat_history"]:
        if role.startswith("👤"):
            st.markdown(f"<div style='text-align:right'><b>{role}:</b> {msg}</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div style='text-align:left;color:yellow'><b>{role}:</b> {msg}</div>", unsafe_allow_html=True)

# --- Tab 2: Banking OTP ---
with tab2:
    st.subheader("🏦 Banking OTP Flow")
    bank_phone = st.text_input("📱 Số điện thoại (virtual):", value=DEFAULT_PHONE, key="bank_phone")
    bank_name = st.selectbox("🏦 Ngân hàng:", ["saigonbank", "hdbank", "vietjetfin", "mock"], key="bank_name")
    account_id = st.text_input("💳 Account ID:", value="", key="bank_account")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("📊 Yêu cầu xem số dư", key="req_balance"):
            try:
                r = requests.post(
                    f"{BANK_API}/bank/{bank_name}/balance",
                    json={"phone": bank_phone, "account_id": account_id},
                    timeout=10,
                )
                data = r.json()
                st.info(data.get("message", "Đã gửi yêu cầu. OTP sẽ được push vào SMS Inbox."))
            except Exception as e:
                st.error(f"❌ Lỗi gửi yêu cầu: {e}")

    with col2:
        st.caption("👉 Không hiển thị OTP trên màn hình này nữa. Xem tab ‘📨 SMS Inbox’.")    

    otp_code = st.text_input("🔑 Nhập OTP:", key="bank_otp")
    if st.button("✅ Xác minh OTP", key="verify_balance"):
        try:
            r = requests.post(
                f"{BANK_API}/bank/{bank_name}/otp/verify",
                json={"phone": bank_phone, "otp": otp_code, "account_id": account_id},
                timeout=10,
            )
            data = r.json()
            if data.get("success"):
                st.success("✅ OTP hợp lệ — Token đã gửi cho Agent để auto-resume")
                st.json(data.get("account_summary"))
            else:
                st.error(f"❌ OTP lỗi: {data.get('reason', 'unknown')}")
        except Exception as e:
            st.error(f"❌ Lỗi xác minh OTP: {e}")

# --- Tab 3: SMS Inbox (nhận bị động qua WS) ---
with tab3:
    st.subheader(f"📨 SMS Inbox (phone: {st.session_state['current_phone']})")

    # 👉 Poll queue và append vào session_state
    while not st.session_state["ws_queue"].empty():
        msg = st.session_state["ws_queue"].get()
        st.session_state["sms_inbox"].append(msg)

    if st.session_state["sms_inbox"]:
        for m in st.session_state["sms_inbox"][::-1]:
            ev = m.get("event")
            pl = m.get("payload", {})
            if ev == "otp_sent":
                st.info(f"🔐 OTP: {pl.get('otp')} | {pl.get('text')}")
            elif ev == "otp_verified":
                st.success(f"✅ OTP verified cho {pl.get('account_id')} — balance: {pl.get('account_summary',{}).get('balance')}")
            elif ev == "otp_failed":
                st.error(f"❌ OTP failed ({pl.get('reason')}) cho {pl.get('account_id')}")
            else:
                st.write(m)
    else:
        st.write("📭 Chưa có SMS nào.")
