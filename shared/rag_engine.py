"""
Frank Bot — RAG Engine
Chunking, embedding, storage, and retrieval using ChromaDB + Voyage AI (via Anthropic).
Drop-in replacement for the static KB context-stuffing approach.
"""
import os, re, json, hashlib
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
CHUNK_SIZE    = 400    # words per chunk
CHUNK_OVERLAP = 80     # word overlap between chunks
TOP_K         = 15     # chunks to retrieve per query

# ── ChromaDB setup (local embeddings — no API key needed) ────────────────────
def get_chroma_collection(collection_name: str, persist_dir: str):
    import chromadb
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    client = chromadb.PersistentClient(path=persist_dir)
    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=DefaultEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"}
    )

# ── Chunker ───────────────────────────────────────────────────────────────────
def chunk_text(text: str, source: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """
    Split text into overlapping word-boundary chunks.
    Respects paragraph breaks where possible.
    """
    # Clean whitespace
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Split into paragraphs first
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

    chunks = []
    current_words = []
    current_para_count = 0

    for para in paragraphs:
        para_words = para.split()
        current_words.extend(para_words)
        current_para_count += 1

        # Flush repeatedly until below chunk_size
        while len(current_words) >= chunk_size:
            chunk_text_str = " ".join(current_words[:chunk_size])
            chunks.append({
                "text": f"[Source: {source}]\n{chunk_text_str}",
                "source": source,
                "chunk_index": len(chunks),
                "word_count": len(current_words[:chunk_size]),
            })
            # Keep overlap
            current_words = current_words[chunk_size - overlap:]
            current_para_count = 0

    # Flush remainder — minimum 1 word (short snippets must be indexable)
    if current_words and len(current_words) >= 1:
        chunks.append({
            "text": f"[Source: {source}]\n" + " ".join(current_words),
            "source": source,
            "chunk_index": len(chunks),
            "word_count": len(current_words),
        })

    return chunks


def chunk_document(title: str, content: str) -> list[dict]:
    """Chunk a single document (title + content)."""
    # Prepend title to first chunk for context
    full_text = f"{title}\n\n{content}"
    return chunk_text(full_text, source=title)


# ── RAG Store ─────────────────────────────────────────────────────────────────
class FrankRAGStore:
    """
    RAG store for a Frank Bot instance.
    Uses ChromaDB's built-in local embeddings (sentence-transformers).
    No external API key required for embeddings — fully self-hosted.
    """

    def __init__(self, bot_id: str, persist_dir: str, api_key: str = ""):
        self.bot_id = bot_id
        self.persist_dir = persist_dir
        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self.collection = get_chroma_collection(
            collection_name=f"frank_{bot_id}",
            persist_dir=persist_dir
        )

    def _doc_id(self, source: str, chunk_index: int) -> str:
        h = hashlib.md5(f"{source}:{chunk_index}".encode()).hexdigest()[:8]
        return f"{h}_{chunk_index}"

    def index_document(self, title: str, content: str, metadata: dict = None) -> int:
        """Chunk and store a document. Chroma handles embeddings. Returns chunk count.
        Always deletes existing chunks for this source first so re-uploads are clean —
        no stale chunks survive if chunk count changes between extractions."""
        chunks = chunk_document(title, content)
        if not chunks:
            return 0

        # Purge any existing chunks for this source before re-indexing.
        # This ensures a re-upload always produces a clean, consistent index
        # even if the new extraction yields a different number of chunks.
        self.delete_document(title)

        texts = [c["text"] for c in chunks]
        ids   = [self._doc_id(title, c["chunk_index"]) for c in chunks]
        metas = [{
            "source": title,
            "chunk_index": c["chunk_index"],
            "word_count": c["word_count"],
            **(metadata or {})
        } for c in chunks]

        # Chroma embeds automatically using DefaultEmbeddingFunction
        self.collection.upsert(ids=ids, documents=texts, metadatas=metas)
        return len(chunks)

    def index_policy_list(self, policies: list[dict]) -> dict:
        results = {}
        for policy in policies:
            n = self.index_document(policy["title"], policy["content"])
            results[policy["title"]] = n
            print(f"  ✅ '{policy['title']}' → {n} chunks")
        return results

    def retrieve(self, query: str, top_k: int = TOP_K) -> list[dict]:
        """Retrieve top-k relevant chunks for a query."""
        if self.collection.count() == 0:
            return []

        results = self.collection.query(
            query_texts=[query],
            n_results=min(top_k, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        chunks = []
        for i, doc in enumerate(results["documents"][0]):
            chunks.append({
                "text": doc,
                "source": results["metadatas"][0][i].get("source", "Unknown"),
                "chunk_index": results["metadatas"][0][i].get("chunk_index", 0),
                "distance": results["distances"][0][i],
                "relevance": round(1 - results["distances"][0][i], 3),
            })
        chunks.sort(key=lambda x: x["distance"])
        return chunks

    def expand_query(self, query: str, api_key: str = "") -> list[str]:
        """
        Use Claude Haiku to generate alternative search phrasings for the query.
        Returns a list of 3-5 queries including the original.
        Falls back gracefully to [query] if the API call fails.
        """
        if not api_key:
            import os
            api_key = os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return [query]

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=200,
                system=(
                    "You generate alternative search queries for a RAG document retrieval system. "
                    "Given a user question, produce 4 short alternative phrasings that would help "
                    "find relevant document chunks. Focus on synonyms, related concepts, and "
                    "document types that might contain the answer. "
                    "Return ONLY a JSON array of strings, no explanation."
                ),
                messages=[{"role": "user", "content": f"Original query: {query}"}],
            )
            import json as _json
            raw = resp.content[0].text.strip()
            # Strip markdown code fences if present
            raw = re.sub(r'^```[a-z]*\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            alternatives = _json.loads(raw.strip())
            if isinstance(alternatives, list):
                # Prepend original, deduplicate
                all_queries = [query] + [q for q in alternatives if q != query]
                return all_queries[:5]
        except Exception:
            pass
        return [query]

    def retrieve_multi(self, queries: list[str], top_k: int = TOP_K) -> list[dict]:
        """
        Run retrieval for multiple query phrasings and merge results.
        Deduplicates by chunk text, keeps best relevance score per chunk.
        """
        if self.collection.count() == 0:
            return []

        seen_texts: dict[str, dict] = {}
        per_query_k = max(5, top_k // len(queries))

        for q in queries:
            for chunk in self.retrieve(q, top_k=per_query_k):
                key = chunk["text"]
                if key not in seen_texts or chunk["relevance"] > seen_texts[key]["relevance"]:
                    seen_texts[key] = chunk

        merged = list(seen_texts.values())
        merged.sort(key=lambda x: x["distance"])
        return merged[:top_k * 2]  # allow more results when multi-query

    def retrieve_by_source(self, source: str, limit: int = 20) -> list[dict]:
        """Retrieve all chunks for a specific source document."""
        if self.collection.count() == 0:
            return []
        results = self.collection.get(
            where={"source": source},
            include=["documents", "metadatas"],
            limit=limit,
        )
        chunks = []
        for i, doc in enumerate(results["documents"]):
            chunks.append({
                "text": doc,
                "source": results["metadatas"][i].get("source", source),
                "chunk_index": results["metadatas"][i].get("chunk_index", 0),
                "distance": 0.0,
                "relevance": 1.0,  # forced inclusion
            })
        chunks.sort(key=lambda x: x["chunk_index"])
        return chunks

    def build_context(self, query: str, top_k: int = TOP_K, min_relevance: float = 0.2,
                      api_key: str = "", expand: bool = True) -> str:
        """
        Build a context string for injection into the system prompt.
        Returns relevant chunks formatted for Claude.

        Query expansion: uses Claude Haiku to generate alternative phrasings,
        runs retrieval for each, merges results. Falls back to single-query if no API key.

        Source-boost: if known document names appear in the query, guarantee
        chunks from those sources are included even if semantic similarity is low.
        """
        # ── Query expansion + multi-query retrieval ───────────────────────────
        if expand and self.collection.count() > 0:
            queries = self.expand_query(query, api_key=api_key)
            if len(queries) > 1:
                chunks = self.retrieve_multi(queries, top_k=top_k)
            else:
                chunks = self.retrieve(query, top_k=top_k)
        else:
            chunks = self.retrieve(query, top_k=top_k)
        if not chunks:
            return ""

        chunks = [c for c in chunks if c["relevance"] >= min_relevance]

        # ── Source boost — pull chunks for explicitly named docs ──────────────
        all_sources = self.list_sources()
        # Strip enrichment tags for matching: "Casual Employee DRAFT.docx [...]" → "Casual Employee DRAFT.docx"
        query_lower = query.lower()
        boosted_sources = set()
        for src in all_sources:
            # Strip synonym tags for matching
            base_src = re.sub(r'\s*\[.*?\]', '', src).lower()
            # Match if 3+ consecutive words from the source name appear in the query
            words = [w for w in base_src.split() if len(w) > 3]
            if len(words) >= 2 and sum(1 for w in words if w in query_lower) >= 2:
                boosted_sources.add(src)

        # Add boosted source chunks (deduplicated by chunk id)
        existing_texts = {c["text"] for c in chunks}
        for src in boosted_sources:
            for bc in self.retrieve_by_source(src, limit=15):
                if bc["text"] not in existing_texts:
                    chunks.append(bc)
                    existing_texts.add(bc["text"])

        if not chunks:
            return ""

        lines = ["## Relevant document content:\n"]
        seen_sources = set()
        for chunk in chunks:
            source = chunk["source"]
            if source not in seen_sources:
                lines.append(f"### {source}")
                seen_sources.add(source)
            lines.append(chunk["text"])
            lines.append("")

        lines.append(f"\n_Sources: {', '.join(seen_sources)}_")
        return "\n".join(lines)

    def count(self) -> int:
        return self.collection.count()

    def list_sources(self) -> list[str]:
        """List all indexed document titles."""
        if self.collection.count() == 0:
            return []
        results = self.collection.get(include=["metadatas"])
        sources = list({m.get("source", "") for m in results["metadatas"]})
        return sorted(sources)

    def list_documents(self) -> list[dict]:
        """Return rich document records for admin panel display.
        Each record: {title, filename, chunks, vision, uploaded_at}"""
        if self.collection.count() == 0:
            return []
        results = self.collection.get(include=["metadatas"])
        # Aggregate per source
        docs: dict[str, dict] = {}
        for m in results["metadatas"]:
            src = m.get("source", "")
            if not src:
                continue
            if src not in docs:
                docs[src] = {
                    "title":       src,
                    "filename":    m.get("filename", src),
                    "chunks":      0,
                    "vision":      bool(m.get("vision", False)),
                    "uploaded_at": m.get("uploaded_at", ""),
                }
            docs[src]["chunks"] += 1
            # Keep vision=True if any chunk has it
            if m.get("vision"):
                docs[src]["vision"] = True
            # Keep the most recent uploaded_at
            if m.get("uploaded_at") and m["uploaded_at"] > docs[src]["uploaded_at"]:
                docs[src]["uploaded_at"] = m["uploaded_at"]
        return sorted(docs.values(), key=lambda d: d["uploaded_at"] or d["title"], reverse=True)

    def delete_document(self, title: str):
        """Remove all chunks for a document."""
        results = self.collection.get(
            where={"source": title},
            include=["documents"],
        )
        if results["ids"]:
            self.collection.delete(ids=results["ids"])
            print(f"  🗑️  Deleted '{title}' ({len(results['ids'])} chunks)")


# ── Fallback: static KB context builder (no RAG) ─────────────────────────────
def build_static_context(policies: list[dict]) -> str:
    """Fallback — inject all policies as static context (original approach)."""
    lines = []
    for p in policies:
        lines.append(f"## {p['title']}\n{p['content']}\n")
    return "\n".join(lines)


# ── Factory ───────────────────────────────────────────────────────────────────
def get_rag_store(bot_id: str, base_dir: str = "/opt/frankbot") -> FrankRAGStore:
    """Get or create a RAG store for a bot instance."""
    persist_dir = str(Path(base_dir) / bot_id / "chroma")
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    return FrankRAGStore(bot_id=bot_id, persist_dir=persist_dir, api_key=api_key)


if __name__ == "__main__":
    # Quick test
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    # Load env
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    # Test with a sample policy
    store = get_rag_store("test-bot", base_dir="/tmp/frankbot_test")
    print(f"Collection count: {store.count()}")

    test_policy = {
        "title": "Leave Policy",
        "content": """Acme Resources Leave Policy

ANNUAL LEAVE
Full-time employees: 4 weeks (20 days) per year, accrued progressively.
Part-time employees: Pro-rata based on ordinary hours worked.
Minimum 2 weeks written notice for planned leave.
Leave can be cashed out by agreement (minimum 4 weeks balance must remain).
Unused leave is paid out on termination.

PERSONAL LEAVE
10 days paid personal/carer's leave per year, accrued progressively.
Medical certificate required for 3 or more consecutive days.

PARENTAL LEAVE
Primary carer: 12 weeks paid leave after 12 months continuous service.
Secondary carer: 2 weeks paid leave.
"""
    }

    print("Indexing test policy...")
    n = store.index_document(test_policy["title"], test_policy["content"])
    print(f"Indexed {n} chunks")

    print("\nRetrieving for query: 'how much annual leave do I get?'")
    chunks = store.retrieve("how much annual leave do I get?", top_k=3)
    for c in chunks:
        print(f"  [{c['relevance']:.2f}] {c['source']}: {c['text'][:100]}...")

    ctx = store.build_context("how much annual leave do I get?")
    print(f"\nContext built: {len(ctx)} chars")
