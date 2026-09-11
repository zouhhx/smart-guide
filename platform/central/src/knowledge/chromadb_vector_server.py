import json
import hashlib
import math
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import chromadb
    from chromadb.utils import embedding_functions
except Exception as exc:
    chromadb = None
    embedding_functions = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


HOST = os.getenv("VECTOR_HOST", "127.0.0.1")
PORT = int(os.getenv("VECTOR_PORT", "3021"))
DATA_DIR = os.getenv(
    "VECTOR_DB_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "chroma")),
)
COLLECTION_NAME = os.getenv("VECTOR_COLLECTION", "exhibition_knowledge")
EMBEDDING_MODEL = os.getenv("VECTOR_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
EMBEDDING_PROVIDER = os.getenv("VECTOR_EMBEDDING_PROVIDER", "hash").strip().lower()
HASH_DIMENSIONS = int(os.getenv("VECTOR_HASH_DIMENSIONS", "384"))


class LocalHashEmbeddingFunction:
    """Offline Chinese-friendly character n-gram embedding for first-stage local vector search."""

    def __init__(self, dimensions=384):
        self.dimensions = max(64, int(dimensions or 384))

    def name(self):
        return f"local-hash-{self.dimensions}"

    def __call__(self, input):
        if isinstance(input, str):
            return self._embed(input)
        return [self._embed(text) for text in input]

    def embed_query(self, input):
        if isinstance(input, str):
            return [self._embed(input)]
        return [self._embed(text) for text in input]

    def embed_documents(self, input):
        return [self._embed(text) for text in input]

    def _embed(self, text):
        value = "".join(str(text or "").lower().split())
        vector = [0.0] * self.dimensions
        if not value:
            return vector
        tokens = []
        tokens.extend(value)
        tokens.extend(value[i:i + 2] for i in range(max(0, len(value) - 1)))
        tokens.extend(value[i:i + 3] for i in range(max(0, len(value) - 2)))
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(item * item for item in vector)) or 1.0
        return [item / norm for item in vector]


class VectorStore:
    def __init__(self):
        if IMPORT_ERROR:
            raise RuntimeError(
                "ChromaDB dependencies are missing. Install with: "
                "python -m pip install -r requirements-vector.txt"
            ) from IMPORT_ERROR
        os.makedirs(DATA_DIR, exist_ok=True)
        self.client = chromadb.PersistentClient(path=DATA_DIR)
        if EMBEDDING_PROVIDER in {"sentence-transformer", "sentence_transformer", "bge"}:
            self.embedding = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL,
                normalize_embeddings=True,
            )
        else:
            self.embedding = LocalHashEmbeddingFunction(HASH_DIMENSIONS)
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self.embedding,
            metadata={"hnsw:space": "cosine"},
        )

    def reset(self):
        try:
            self.client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self.embedding,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, documents):
        ids = []
        texts = []
        metadatas = []
        for doc in documents or []:
            doc_id = str(doc.get("id") or "").strip()
            text = str(doc.get("text") or "").strip()
            if not doc_id or not text:
                continue
            ids.append(doc_id)
            texts.append(text)
            metadatas.append(self._metadata(doc.get("metadata") or {}))
        for start in range(0, len(ids), 128):
            end = start + 128
            self.collection.upsert(
                ids=ids[start:end],
                documents=texts[start:end],
                metadatas=metadatas[start:end],
            )
        return len(ids)

    def search(self, query, limit=10, object_types=None, min_similarity=0.0, metadata_filters=None):
        conditions = []
        object_types = [str(item) for item in (object_types or []) if str(item).strip()]
        if len(object_types) == 1:
            conditions.append({"object_type": object_types[0]})
        elif len(object_types) > 1:
            conditions.append({"object_type": {"$in": object_types}})
        for key, value in (metadata_filters or {}).items():
            if str(key).strip() and str(value).strip():
                conditions.append({str(key): str(value)})
        where = None if not conditions else conditions[0] if len(conditions) == 1 else {"$and": conditions}

        result = self.collection.query(
            query_texts=[query],
            n_results=max(1, min(int(limit or 10), 50)),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        rows = []
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        for doc_id, document, metadata, distance in zip(ids, docs, metas, distances):
            similarity = 1 - float(distance)
            if similarity < float(min_similarity or 0):
                continue
            rows.append({
                "id": doc_id,
                "document": document,
                "metadata": metadata or {},
                "distance": distance,
                "similarity": similarity,
            })
        return rows

    def count(self):
        return self.collection.count()

    def _metadata(self, value):
        result = {}
        for key, raw in value.items():
            if raw is None:
                result[str(key)] = ""
            elif isinstance(raw, (str, int, float, bool)):
                result[str(key)] = raw
            elif isinstance(raw, list):
                result[str(key)] = "、".join(str(item) for item in raw if str(item).strip())
            else:
                result[str(key)] = json.dumps(raw, ensure_ascii=False)
        return result


STORE = None


def get_store():
    global STORE
    if STORE is None:
        STORE = VectorStore()
    return STORE


class Handler(BaseHTTPRequestHandler):
    server_version = "ExhibitionChromaVector/1.0"

    def do_GET(self):
        if self.path != "/health":
            self._json({"ok": False, "error": "not found"}, status=404)
            return
        try:
            store = get_store()
            self._json({
                "ok": True,
                "collection": COLLECTION_NAME,
                "dbDir": DATA_DIR,
                "embeddingProvider": EMBEDDING_PROVIDER,
                "embeddingModel": EMBEDDING_MODEL,
                "count": store.count(),
            })
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, status=500)

    def do_POST(self):
        try:
            payload = self._read_json()
            store = get_store()
            if self.path == "/reset":
                store.reset()
                self._json({"ok": True, "count": store.count()})
                return
            if self.path == "/upsert":
                count = store.upsert(payload.get("documents") or [])
                self._json({"ok": True, "upserted": count, "count": store.count()})
                return
            if self.path == "/search":
                rows = store.search(
                    query=str(payload.get("query") or "").strip(),
                    limit=payload.get("limit") or 10,
                    object_types=payload.get("object_types") or [],
                    min_similarity=payload.get("min_similarity") or 0,
                    metadata_filters=payload.get("metadata_filters") or {},
                )
                self._json({"ok": True, "results": rows, "total": len(rows)})
                return
            self._json({"ok": False, "error": "not found"}, status=404)
        except Exception as exc:
            traceback.print_exc()
            self._json({"ok": False, "error": str(exc)}, status=500)

    def log_message(self, fmt, *args):
        sys.stdout.write("[VectorServer] " + fmt % args + "\n")

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    print(f"[VectorServer] ChromaDB vector service: http://{HOST}:{PORT}")
    print(f"[VectorServer] db={DATA_DIR}")
    print(f"[VectorServer] provider={EMBEDDING_PROVIDER} model={EMBEDDING_MODEL}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
