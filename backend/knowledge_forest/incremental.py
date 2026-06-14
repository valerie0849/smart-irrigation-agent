import os
import json
import hashlib
import pickle
import logging
import time
import numpy as np
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

THETA_MERGE = 0.75
THETA_NEW = 0.50
FULL_REBUILD_RATIO = 0.30
FULL_REBUILD_CHUNK_RATIO = 0.30
FULL_REBUILD_MAX_COUNT = 10
FULL_REBUILD_MAX_DAYS = 30


def _log_json(tag: str, data: dict):
    logger.info(f"[增量] [{tag}] >>>\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}")


def _md5(filepath: str) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class IncrementalUpdater:
    def __init__(self, db: Session, knowledge_base_path: str):
        self.db = db
        self.knowledge_base_path = knowledge_base_path
        self.documents: List[Dict[str, Any]] = []
        self.hierarchy: Dict[str, list] = {"L0": [], "L1": [], "L2": []}
        self.embedding = None

    def load_state(self):
        from backend.knowledge_forest.embedding import TextEmbedding

        if os.path.exists("./faiss_docs.pkl"):
            with open("./faiss_docs.pkl", "rb") as f:
                data = pickle.load(f)
                self.documents = data.get("documents", [])
                self.hierarchy = data.get("hierarchy", {"L0": [], "L1": [], "L2": []})

        self.embedding = TextEmbedding(model_path="./models")
        if not self.embedding.is_fitted:
            texts = [d.get("content", "") for d in self.documents]
            if texts:
                self.embedding.fit(texts)

    def save_state(self):
        source_tag_map = {}
        for d in self.documents:
            src = d.get("source", "")
            tag = d.get("tag", "")
            if src and src not in source_tag_map:
                source_tag_map[src] = tag

        with open("./faiss_docs.pkl", "wb") as f:
            pickle.dump({
                "documents": self.documents,
                "hierarchy": self.hierarchy,
                "source_tag_map": source_tag_map,
            }, f)

    def detect_new_documents(self) -> List[str]:
        from backend.src.models import ProcessedDocument

        t0 = time.time()
        known = {}
        for row in self.db.query(ProcessedDocument).all():
            known[row.filename] = row.md5_hash

        new_files = []
        if not os.path.exists(self.knowledge_base_path):
            os.makedirs(self.knowledge_base_path, exist_ok=True)
            return new_files

        all_files = sorted(os.listdir(self.knowledge_base_path))
        for fname in all_files:
            fpath = os.path.join(self.knowledge_base_path, fname)
            if not os.path.isfile(fpath):
                continue
            if fname.startswith("~$") or fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in (".pdf", ".txt", ".md", ".doc", ".docx"):
                continue

            if fname in known:
                current_hash = _md5(fpath)
                known_hash = known[fname]
                if known_hash and current_hash == known_hash:
                    continue
                logger.info(f"[增量] {fname} 哈希不同或首次记录")

            new_files.append(fname)

        _log_json("DETECT_DONE", {
            "known_count": len(known),
            "total_files": len(all_files),
            "new_files": len(new_files),
            "new_list": new_files,
            "elapsed_sec": round(time.time() - t0, 2),
        })
        return new_files

    def should_full_rebuild(self, new_files: List[str]) -> bool:
        if not self.documents:
            return True

        existing_sources = set()
        for d in self.documents:
            src = d.get("source", "")
            if src:
                existing_sources.add(src)

        source_ratio = len(new_files) / max(len(existing_sources), 1)
        trigger_source = source_ratio >= FULL_REBUILD_RATIO

        existing_chunks = len(self.documents)
        new_chunk_estimate = len(new_files) * 10
        chunk_ratio = new_chunk_estimate / max(existing_chunks, 1)
        trigger_chunk = chunk_ratio >= FULL_REBUILD_CHUNK_RATIO

        from backend.src.models import ProcessedDocument
        processed_count = self.db.query(ProcessedDocument).count()
        trigger_count = processed_count + len(new_files) >= FULL_REBUILD_MAX_COUNT

        trigger_days = False
        try:
            oldest = self.db.query(ProcessedDocument).order_by(
                ProcessedDocument.processed_at.asc()
            ).first()
            if oldest and oldest.processed_at:
                from datetime import datetime
                days = (datetime.now() - oldest.processed_at).days
                trigger_days = days >= FULL_REBUILD_MAX_DAYS
        except Exception:
            pass

        trigger = trigger_source or trigger_chunk or trigger_count or trigger_days

        _log_json("REBUILD_CHECK", {
            "existing_sources": len(existing_sources),
            "new_files": len(new_files),
            "source_ratio": round(source_ratio, 3),
            "chunk_ratio": round(chunk_ratio, 3),
            "processed_count": processed_count,
            "trigger_source": trigger_source,
            "trigger_chunk": trigger_chunk,
            "trigger_count": trigger_count,
            "trigger_days": trigger_days,
            "should_full_rebuild": trigger,
        })
        return trigger

    def process_single_new_document(
        self, filename: str
    ) -> Tuple[List[Dict], List[Dict], str]:
        from backend.knowledge_forest.parser import DocumentParser
        from backend.knowledge_forest.splitter import TextSplitter
        from backend.knowledge_forest.summary import SummaryGenerator

        t0 = time.time()
        fpath = os.path.join(self.knowledge_base_path, filename)
        _log_json("INCREMENTAL_DOC", {
            "file": filename,
            "action": "开始处理",
        })

        parser = DocumentParser()
        raw_docs = parser.parse_document(fpath)
        if not raw_docs:
            logger.warning(f"[增量] 解析失败: {filename}")
            return [], [], filename

        splitter = TextSplitter()
        chunks = splitter.split_documents(raw_docs)
        doc_tag = self._classify_tag(filename)
        for c in chunks:
            c["level"] = 0
            c["source"] = filename
            c["tag"] = doc_tag

        if not self.embedding or not self.embedding.is_fitted:
            texts = [c["content"] for c in chunks]
            self.embedding.fit(texts)

        texts = [c["content"] for c in chunks]
        chunk_embs = self.embedding.batch_encode(texts)
        for chunk, emb in zip(chunks, chunk_embs):
            chunk["embedding"] = emb

        _log_json("INCREMENTAL_SPLIT", {
            "file": filename,
            "chunks": len(chunks),
            "embed_dim": int(chunk_embs.shape[1]) if len(chunks) > 0 else 0,
            "elapsed_sec": round(time.time() - t0, 2),
        })

        summary_gen = SummaryGenerator()
        l1_summaries = summary_gen.per_document_summarize(
            chunks, chunk_embs, filename
        )

        for s in l1_summaries:
            s.setdefault("source", filename)
            s.setdefault("level", 1)
            s.setdefault("tag", doc_tag)
            if "embedding" not in s:
                s["embedding"] = self.embedding.encode(s["content"])

        total_elapsed = round(time.time() - t0, 2)
        _log_json("INCREMENTAL_DOC_DONE", {
            "file": filename,
            "l0_chunks": len(chunks),
            "l1_summaries": len(l1_summaries),
            "elapsed_sec": total_elapsed,
        })

        return chunks, l1_summaries, filename

    def match_and_decide(
        self, new_l1_summaries: List[Dict], filename: str
    ) -> List[Dict[str, Any]]:
        existing_l1 = [d for d in self.documents if d.get("level") == 1]
        if not existing_l1:
            _log_json("MATCH_DECISION", {
                "file": filename,
                "decision": "new_cluster",
                "reason": "no_existing_L1",
            })
            return [{"decision": "new_cluster", "cluster_id": len(existing_l1)}]

        decisions = []
        for l1_summary in new_l1_summaries:
            new_emb = l1_summary.get("embedding")
            if new_emb is None:
                new_emb = self.embedding.encode(l1_summary.get("content", ""))
                l1_summary["embedding"] = new_emb

            max_sim = -1.0
            best_match = None
            for existing in existing_l1:
                exist_emb = existing.get("embedding")
                if exist_emb is None:
                    exist_emb = np.zeros(self.embedding.get_dimension(), dtype=np.float32)
                sim = float(np.dot(new_emb, exist_emb))
                if sim > max_sim:
                    max_sim = sim
                    best_match = existing

            if max_sim >= THETA_MERGE:
                decision = {
                    "decision": "merge",
                    "similarity": round(max_sim, 4),
                    "target_cluster_id": best_match.get("cluster_id", 0),
                    "target_summary_id": id(best_match),
                }
            elif max_sim >= THETA_NEW:
                decision = {
                    "decision": "semantic_link",
                    "similarity": round(max_sim, 4),
                    "target_cluster_id": best_match.get("cluster_id", 0),
                }
            else:
                decision = {
                    "decision": "new_cluster",
                    "similarity": round(max_sim, 4),
                    "cluster_id": len(existing_l1) + len(decisions),
                }
            decisions.append(decision)

        _log_json("MATCH_DECISION", {
            "file": filename,
            "l1_count": len(new_l1_summaries),
            "existing_l1_count": len(existing_l1),
            "decisions": [
                {"idx": i, "decision": d["decision"], "sim": d.get("similarity")}
                for i, d in enumerate(decisions)
            ],
        })
        return decisions

    def update_summaries(
        self,
        l0_chunks: List[Dict],
        l1_summaries: List[Dict],
        decisions: List[Dict],
        filename: str,
    ):
        from backend.knowledge_forest.summary import SummaryGenerator

        self.documents.extend(l0_chunks)
        self.hierarchy.setdefault("L0", []).extend(l0_chunks)

        summary_gen = SummaryGenerator()
        new_l1_docs = []
        affected_cluster_ids = set()

        for i, (l1_summary, decision) in enumerate(zip(l1_summaries, decisions)):
            if decision["decision"] == "merge":
                cid = decision["target_cluster_id"]
                affected_cluster_ids.add(cid)
                l1_summary["cluster_id"] = cid
                l1_summary["source"] = filename
                new_l1_docs.append(l1_summary)

            elif decision["decision"] == "semantic_link":
                related_cid = decision["target_cluster_id"]
                new_cid = max(
                    [d.get("cluster_id", 0) for d in self.documents], default=0
                ) + 1
                l1_summary["cluster_id"] = new_cid
                l1_summary["source"] = filename
                l1_summary.setdefault("metadata", {})
                l1_summary["metadata"].setdefault("semantic_links", []).append(
                    related_cid
                )
                new_l1_docs.append(l1_summary)

            else:
                new_cid = max(
                    [d.get("cluster_id", 0) for d in self.documents], default=0
                ) + 1
                l1_summary["cluster_id"] = new_cid
                l1_summary["source"] = filename
                new_l1_docs.append(l1_summary)

        for cid in affected_cluster_ids:
            cluster_docs = [
                d for d in self.documents
                if d.get("cluster_id") == cid and d.get("level") == 1
            ]
            cluster_l0 = [
                d for d in self.documents
                if d.get("cluster_id") == cid and d.get("level") == 0
            ]
            if cluster_docs:
                texts = [d.get("content", "") for d in cluster_docs[:5]]
                new_summary = summary_gen._llm_summarize(texts, max_length=400)
                if new_summary:
                    primary = cluster_docs[0]
                    primary["content"] = new_summary
                    primary["embedding"] = self.embedding.encode(new_summary)
                    logger.info(f"[增量] 重写 cluster_{cid} 摘要")

        self.documents.extend(new_l1_docs)
        self.hierarchy.setdefault("L1", []).extend(new_l1_docs)

        _log_json("SUMMARIES_UPDATED", {
            "file": filename,
            "l0_added": len(l0_chunks),
            "l1_added": len(new_l1_docs),
            "l1_rewritten_clusters": list(affected_cluster_ids),
            "decisions": [{"idx": i, "d": d["decision"]} for i, d in enumerate(decisions)],
        })

    def update_index_and_db(self, filename: str, filepath: str):
        from backend.src.models import ProcessedDocument
        import faiss

        t0 = time.time()
        dim = self.embedding.get_dimension()
        all_embs = []
        for d in self.documents:
            emb = d.get("embedding")
            if emb is None:
                emb = self.embedding.encode(d.get("content", ""))
            all_embs.append(emb)
        emb_array = np.array(all_embs, dtype=np.float32)

        index = faiss.IndexIDMap(faiss.IndexFlatL2(dim))
        ids = np.arange(emb_array.shape[0], dtype=np.int64)
        index.add_with_ids(emb_array, ids)
        faiss.write_index(index, "./faiss_index.bin")

        self.save_state()
        _log_json("FAISS_UPDATED", {
            "total_vectors": int(emb_array.shape[0]),
            "dim": dim,
            "index_type": "IndexIDMap(IndexFlatL2)",
            "elapsed_sec": round(time.time() - t0, 2),
        })

        file_hash = _md5(filepath)
        existing = (
            self.db.query(ProcessedDocument)
            .filter(ProcessedDocument.filename == filename)
            .first()
        )
        if existing:
            existing.md5_hash = file_hash
            existing.processed_at = datetime.now()
        else:
            self.db.add(ProcessedDocument(
                filename=filename,
                md5_hash=file_hash,
                doc_source=filename,
                processed_at=datetime.now(),
            ))
        self.db.commit()

        from backend.src.models import KnowledgeNode
        for doc in self.documents:
            meta = {
                "source": doc.get("source", ""),
                "page": doc.get("page", 0),
                "cluster_id": doc.get("cluster_id", 0),
                "level": doc.get("level", 0),
                "child_ids": doc.get("child_ids", []),
                "chunk_id": doc.get("chunk_id", ""),
                "raw_content": (doc.get("raw_content", "") or "")[:200],
            }
            tag = self._classify_tag(doc.get("source", ""))
            existing_node = (
                self.db.query(KnowledgeNode)
                .filter(
                    KnowledgeNode.metadata_info.like(f'%"source": "{doc.get("source", "")}"%'),
                    KnowledgeNode.level == doc.get("level", 0),
                )
                .first()
            )
            if not existing_node:
                node = KnowledgeNode(
                    level=doc.get("level", 0),
                    content=(doc.get("content", "") or "")[:5000],
                    metadata_info=json.dumps(meta, ensure_ascii=False),
                    parent_id=doc.get("parent_id"),
                    cluster_id=doc.get("cluster_id", 0),
                    tag=tag,
                )
                self.db.add(node)
        self.db.commit()
        _log_json("SQLITE_UPDATED", {"file": filename, "total_nodes": len(self.documents)})

    def _classify_tag(self, source: str) -> str:
        from backend.knowledge_forest.tags import classify_document_tag
        return classify_document_tag(source, "")

    async def incremental_update(self) -> Dict[str, Any]:
        import asyncio

        forest_start = time.time()
        logger.info("=" * 60)
        logger.info("[增量] ========== 增量更新流程启动 ==========")

        self.load_state()

        new_files = self.detect_new_documents()
        if not new_files:
            _log_json("INCREMENTAL_SKIP", {"reason": "无新文献"})
            return {"action": "skip", "new_files": 0}

        if self.should_full_rebuild(new_files):
            _log_json("FULL_REBUILD_TRIGGER", {
                "reason": f"新增{len(new_files)}篇≥阈值",
                "new_files": new_files,
                "total_elapsed_sec": round(time.time() - forest_start, 2),
            })
            return {"action": "full_rebuild", "new_files": len(new_files)}

        for filename in new_files:
            fpath = os.path.join(self.knowledge_base_path, filename)
            if not os.path.isfile(fpath):
                continue

            l0_chunks, l1_summaries, _ = self.process_single_new_document(filename)
            if not l0_chunks:
                continue

            decisions = self.match_and_decide(l1_summaries, filename)
            self.update_summaries(l0_chunks, l1_summaries, decisions, filename)
            self.update_index_and_db(filename, fpath)

        total_elapsed = round(time.time() - forest_start, 2)
        result = {
            "action": "incremental",
            "new_files": len(new_files),
            "total_nodes": len(self.documents),
            "l0": len(self.hierarchy.get("L0", [])),
            "l1": len(self.hierarchy.get("L1", [])),
            "l2": len(self.hierarchy.get("L2", [])),
            "total_elapsed_sec": total_elapsed,
        }
        _log_json("INCREMENTAL_COMPLETE", result)
        return result