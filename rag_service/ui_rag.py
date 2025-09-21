# ui_rag.py
import streamlit as st
import requests
import os

RAG_API = os.getenv("RAG_API", "http://localhost:8002")

st.set_page_config(page_title="📚 Milvus RAG Demo", layout="wide")
st.title("📚 Milvus RAG Service UI")

tab1, tab2 = st.tabs(["➕ Thêm tài liệu", "🔍 Tìm kiếm"])

# --- Tab 1: Add ---
with tab1:
    st.subheader("Thêm văn bản vào collection (bank)")

    bank = st.text_input("Tên ngân hàng (collection)", "mock_bank")
    text = st.text_area("Văn bản")
    meta_key = st.text_input("Metadata key", "source")
    meta_val = st.text_input("Metadata value", "manual")

    if st.button("📥 Thêm vào Milvus"):
        bank = bank.strip().lower()
        if not bank:
            st.warning("⚠️ Vui lòng nhập tên collection.")
        elif not text.strip():
            st.warning("⚠️ Vui lòng nhập văn bản trước khi thêm.")
        else:
            payload = {
                "bank_name": bank,
                "texts": [text.strip()],
                "metadatas": [{meta_key.strip(): meta_val.strip()}],
            }
            try:
                r = requests.post(f"{RAG_API}/rag/add", json=payload, timeout=15)
                r.raise_for_status()
                st.success(r.json())
            except Exception as e:
                st.error(f"Lỗi khi thêm vào Milvus: {e}")

# --- Tab 2: Search ---
with tab2:
    st.subheader("Tìm kiếm ngữ nghĩa")

    bank = st.text_input("Tên ngân hàng (collection)", "mock_bank", key="search_bank")
    query = st.text_input("Câu truy vấn", "thẻ tín dụng")
    topk = st.slider("Top K", 1, 10, 5)

    if st.button("🔍 Tìm"):
        bank = bank.strip().lower()
        payload = {"bank_name": bank, "query": query.strip(), "k": topk}
        try:
            r = requests.post(f"{RAG_API}/rag/search", json=payload, timeout=15)
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                st.info("❌ Không tìm thấy kết quả nào.")
            else:
                for idx, res in enumerate(results, 1):
                    st.markdown(f"**{idx}. {res.get('text')}** (distance={res.get('distance'):.4f})")
                    with st.expander("📌 Metadata"):
                        st.json(res)
        except Exception as e:
            st.error(f"Lỗi khi tìm kiếm: {e}")
