from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any
import os, json
import numpy as np

from pymilvus import (
    connections,
    FieldSchema, CollectionSchema, DataType,
    Collection,
    utility
)
from sentence_transformers import SentenceTransformer

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = os.getenv("MILVUS_PORT", "19530")

app = FastAPI(title="RAG Service", description="Milvus-backed semantic search API")

# --- Kết nối ---
@app.on_event("startup")
def _startup_event() -> None:
    if not connections.has_connection("default"):
        print(f"🔌 Connecting to Milvus at {MILVUS_HOST}:{MILVUS_PORT}")
        connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)

@app.on_event("shutdown")
def _shutdown_event() -> None:
    if connections.has_connection("default"):
        connections.disconnect(alias="default")

# --- Embedding model ---
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
model = SentenceTransformer(MODEL_NAME)
VECTOR_DIMENSION = model.get_sentence_embedding_dimension()


def ensure_collection(bank_name: str) -> Collection:
    """
    Đảm bảo collection tồn tại với đúng tên bank_name user nhập.
    Nếu chưa có thì tạo mới.
    """
    name = bank_name.lower().strip()
    if not utility.has_collection(name):
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=4096),
        ]
        schema = CollectionSchema(fields, description=f"Collection for {bank_name}")
        coll = Collection(name=name, schema=schema, using="default", shards_num=1)
        coll.create_index("embedding", {
            "index_type": "HNSW", "metric_type": "L2",
            "params": {"M": 16, "efConstruction": 200}
        })
        coll.load()
        return coll
    coll = Collection(name, using="default")
    if not coll.indexes:
        coll.create_index("embedding", {
            "index_type": "HNSW", "metric_type": "L2",
            "params": {"M": 16, "efConstruction": 200}
        })
    coll.load()
    return coll


def chunk_text(text: str, max_chars: int = 1000, overlap: int = 100) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
    return chunks


# --- Schemas ---
class AddRequest(BaseModel):
    bank_name: str
    texts: List[str]
    metadatas: List[Dict[str, Any]]

class SearchRequest(BaseModel):
    bank_name: str
    query: str
    k: int = 5
    max_distance: float = 1.5


# --- Endpoints ---
@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok"}

@app.get("/rag/collections")
def list_collections() -> Dict[str, List[str]]:
    return {"collections": utility.list_collections()}

@app.post("/rag/add")
def rag_add(req: AddRequest) -> Dict[str, Any]:
    if len(req.texts) != len(req.metadatas):
        raise HTTPException(400, "texts and metadatas must match length")
    coll = ensure_collection(req.bank_name)

    final_texts, final_metas = [], []
    for text, meta in zip(req.texts, req.metadatas):
        chunks = chunk_text(text)
        for i, ch in enumerate(chunks):
            final_texts.append(ch)
            final_metas.append({**meta, "chunk_id": i, "orig_len": len(text)})

    embeds = model.encode(final_texts, convert_to_numpy=True)
    meta_strings = [json.dumps(m, ensure_ascii=False) for m in final_metas]
    result = coll.insert([embeds.tolist(), final_texts, meta_strings])
    return {"ids": list(result.primary_keys), "chunks": len(final_texts)}

@app.post("/rag/search")
def rag_search(req: SearchRequest) -> Dict[str, Any]:
    name = req.bank_name.lower().strip()
    if not utility.has_collection(name):
        return {"results": []}
    coll = Collection(name, using="default")
    coll.load()

    q_emb = model.encode([req.query], convert_to_numpy=True)
    res = coll.search(q_emb.tolist(), "embedding",
                      {"metric_type": "L2", "params": {"ef": 64}},
                      limit=req.k,
                      output_fields=["text", "metadata"])
    results = []
    for hits in res:
        for hit in hits:
            if float(hit.distance) <= req.max_distance:
                meta = {}
                try:
                    meta = json.loads(hit.entity.get("metadata") or "{}")
                except:
                    pass
                results.append({
                    "text": hit.entity.get("text"),
                    "distance": float(hit.distance),
                    **meta
                })
    return {"results": results}
