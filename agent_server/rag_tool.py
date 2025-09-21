# rag_tool.py
from gemini_planner import _model
from pymilvus import utility, connections
import os
from config import MILVUS_HOST, MILVUS_PORT



# Hàm helper: đảm bảo đã connect
def ensure_connected():
    if not connections.has_connection("default"):
        connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)

def get_collection_name(bank_name: str) -> str:
    return f"bank_{bank_name.lower()}"

def resolve_bank_collection(user_bank: str) -> str:
    """Map tên ngân hàng user nhập sang collection hiện có trong Milvus."""
    ensure_connected()
    all_colls = utility.list_collections()
    print("RAG: ", all_colls)
    prompt = f"""
    Người dùng muốn truy vấn ngân hàng: "{user_bank}".
    Đây là danh sách collection có trong Milvus: {all_colls}.
    Hãy chọn 1 collection phù hợp nhất.
    ⚠️ Chỉ được trả về CHÍNH XÁC 1 tên từ danh sách trên (copy y nguyên).
    Nếu không có cái nào phù hợp thì trả về None.
    """
    resp = _model.generate_content(prompt)
    text = resp.text.strip()
    print("text AI promt: ",text)
    if text in all_colls:
        return text
    return None
