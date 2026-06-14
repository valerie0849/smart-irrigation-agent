import os
import json
import pickle
import logging
import time
import numpy as np
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from backend.knowledge_forest.tags import (
    classify_document_tag,
    classify_all_documents,
    compute_cluster_tag_ratios,
    get_tag_label,
    TAG_DEFINITIONS,
)


def _log_json(tag: str, data: dict):
    logger.info(f"[{tag}] >>>\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}")


class KnowledgeForest:
    def __init__(self, db: Session, knowledge_base_path: str = None):
        self.db = db
        self.knowledge_base_path = knowledge_base_path or os.environ.get(
            "KNOWLEDGE_BASE_PATH", "./knowledge_base"
        )
        self.embedding = None
        self.documents = []
        self.cluster_ids = set()
        self.is_built = False
        self.hierarchy = {"L0": [], "L1": [], "L2": []}

    async def build_forest(self):
        """构建知识森林（重构后，使用管道模式）"""
        from backend.knowledge_forest.pipeline import ForestBuildPipeline

        pipeline = ForestBuildPipeline(self.db, self.knowledge_base_path)
        result = await pipeline.execute()

        if result.get("total_nodes", 0) > 0:
            self.is_built = True
            self.documents = pipeline.context.all_nodes
            self.hierarchy = pipeline.context.hierarchy
            self.cluster_ids = pipeline.context.cluster_ids
            self.source_tag_map = pipeline.context.source_tag_map
            self.embedding = pipeline.context.embedding

        return result

    def try_load(self) -> bool:
        from backend.src.models import KnowledgeNode
        import faiss

        node_count = self.db.query(KnowledgeNode).count()
        if node_count == 0:
            logger.info("[知识森林] 数据库无节点，需重建")
            return False

        if os.path.exists("./faiss_docs.pkl"):
            try:
                with open("./faiss_docs.pkl", "rb") as f:
                    data = pickle.load(f)
                    self.documents = data.get("documents", [])
                    self.hierarchy = data.get("hierarchy", {"L0": [], "L1": [], "L2": []})
                    self.source_tag_map = data.get("source_tag_map", {})

                    if not self.source_tag_map and self.documents:
                        logger.info("[知识森林] 旧缓存缺少标签数据，自动补全...")
                        self.source_tag_map = {}
                        for d in self.documents:
                            src = d.get("source", "")
                            if src and src not in self.source_tag_map:
                                self.source_tag_map[src] = classify_document_tag(src, d.get("content", "")[:500])
                            if src and not d.get("tag"):
                                d["tag"] = self.source_tag_map.get(src, "domain_knowledge")
                        logger.info(f"[知识森林] 自动补全标签: {len(self.source_tag_map)}个来源")
                self.cluster_ids = set(d.get("cluster_id", 0) for d in self.documents)
                from backend.knowledge_forest.retrieval import KnowledgeRetrieval
                retrieval = KnowledgeRetrieval()
                retrieval.documents = self.documents
                retrieval.load_index("./faiss_index.bin")
                self.embedding = __import__(
                    "backend.knowledge_forest.embedding", fromlist=["TextEmbedding"]
                ).TextEmbedding(model_path="./models")
                if not self.embedding.is_fitted:
                    texts = [d.get("content", "") for d in self.documents]
                    self.embedding.fit(texts)
                self.is_built = True
                logger.info(f"[知识森林] 从缓存加载: L0={len(self.hierarchy['L0'])} L1={len(self.hierarchy['L1'])} L2={len(self.hierarchy['L2'])}")

                self._backfill_processed_docs()
                self._try_incremental_update()

                return True
            except Exception as e:
                logger.warning(f"[知识森林] 加载失败: {e}")

        logger.info("[知识森林] 缓存不存在，需重建")
        return False

    def _backfill_processed_docs(self):
        try:
            from backend.src.models import ProcessedDocument
            from datetime import datetime
            import hashlib

            def _md5(fp):
                h = hashlib.md5()
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        h.update(chunk)
                return h.hexdigest()

            known = {row.filename: row.md5_hash for row in self.db.query(ProcessedDocument).all()}
            sources = set()
            for d in self.documents:
                src = d.get("source", "")
                if src and src not in known and src not in sources:
                    sources.add(src)

            added = 0
            for src in sources:
                if "," in src:
                    continue
                fpath = os.path.join(self.knowledge_base_path, src)
                md5_val = _md5(fpath) if os.path.isfile(fpath) else ""
                self.db.add(ProcessedDocument(
                    filename=src,
                    md5_hash=md5_val,
                    doc_source=src,
                    processed_at=datetime.now(),
                ))
                added += 1
            self.db.commit()

            if added:
                logger.info(f"[知识森林] 回填已处理文献: {added}篇, 含MD5指纹")
            if known:
                logger.info(f"[知识森林] processed_documents表已有: {len(known)}篇")
        except Exception as e:
            logger.warning(f"[知识森林] 回填失败: {e}")

    def _try_incremental_update(self):
        from backend.knowledge_forest.incremental import IncrementalUpdater
        import threading

        updater = IncrementalUpdater(self.db, self.knowledge_base_path)
        new_files = updater.detect_new_documents()
        if not new_files:
            return

        logger.info(f"[知识森林] 检测到 {len(new_files)} 篇新文献，启动后台增量更新...")
        self._run_incremental_update(updater, new_files)

    def _run_incremental_update(self, updater, new_files):
        import threading

        def _run():
            import asyncio
            import logging
            lg = logging.getLogger("backend.knowledge_forest.main")
            try:
                from backend.src.models import get_db
                new_db = next(get_db())
                updater.db = new_db
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    result = loop.run_until_complete(updater.incremental_update())
                    if result.get("action") == "full_rebuild":
                        lg.info("[知识森林] 触发全量重建，正在自动执行...")
                        old_db = self.db
                        self.db = new_db
                        try:
                            loop.run_until_complete(self.build_forest())
                            self._backfill_processed_docs()
                            lg.info("[知识森林] 全量重建完成")
                        finally:
                            self.db = old_db
                    elif result.get("action") == "incremental":
                        self.documents = updater.documents
                        self.hierarchy = updater.hierarchy
                        self.cluster_ids = set(d.get("cluster_id", 0) for d in self.documents)
                        self.embedding = updater.embedding
                        self.is_built = True
                        lg.info("[知识森林] 后台增量更新完成")
                finally:
                    new_db.close()
            except Exception as e:
                lg.error(f"[知识森林] 后台增量更新失败: {e}")

        threading.Thread(target=_run, daemon=True).start()

    async def incremental_update_forest(self) -> Dict[str, Any]:
        from backend.knowledge_forest.incremental import IncrementalUpdater

        updater = IncrementalUpdater(self.db, self.knowledge_base_path)

        if not self.is_built:
            if not self.try_load():
                return await self.build_forest()

        result = await updater.incremental_update()

        if result.get("action") == "full_rebuild":
            logger.info("[知识森林] 增量更新触发全量重建")
            self.is_built = False
            self.documents = []
            self.hierarchy = {"L0": [], "L1": [], "L2": []}
            return await self.build_forest()

        if result.get("action") == "incremental":
            self.documents = updater.documents
            self.hierarchy = updater.hierarchy
            self.cluster_ids = set(d.get("cluster_id", 0) for d in self.documents)
            self.embedding = updater.embedding
            self.is_built = True

            from backend.knowledge_forest.retrieval import KnowledgeRetrieval
            retrieval = KnowledgeRetrieval()
            retrieval.documents = self.documents
            retrieval.load_index("./faiss_index.bin")

        return result

    async def query(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        import faiss
        from backend.knowledge_forest.retrieval import KnowledgeRetrieval

        t0 = time.time()
        logger.info(f"[知识森林] Ψ-RAG检索: '{query[:80]}'")

        if not self.is_built:
            if not self.try_load():
                await self.build_forest()

        query_vec = self.embedding.encode(query).astype(np.float32)

        retrieval = KnowledgeRetrieval()
        retrieval.documents = self.documents
        retrieval.load_index("./faiss_index.bin")
        results = retrieval.hierarchical_search(query_vec, self.documents, k=k, query_text=query)

        output = []
        for r in results:
            output.append({
                "content": r.get("content", ""),
                "source": r.get("source", ""),
                "similarity": round(float(r.get("similarity", 0)), 4),
                "layer": r.get("layer", "L0"),
                "level": r.get("level", 0),
                "cluster_id": r.get("cluster_id", 0),
                "chunk_id": r.get("chunk_id", ""),
                "child_ids": r.get("child_ids", []),
                "tag": r.get("tag", "domain_knowledge"),
                "dominant_tag": r.get("dominant_tag", ""),
            })

        l0 = sum(1 for r in output if r["level"] == 0)
        l1 = sum(1 for r in output if r["level"] == 1)
        l2 = sum(1 for r in output if r["level"] >= 2)
        elapsed_ms = int((time.time() - t0) * 1000)
        _log_json("QUERY_DONE", {
            "query": query[:80],
            "k": k,
            "results": len(output),
            "layer_breakdown": {"L0": l0, "L1": l1, "L2": l2},
            "elapsed_ms": elapsed_ms,
            "top3_similarities": [o["similarity"] for o in output[:3]],
            "top3_sources": [o["source"] for o in output[:3]],
        })

        return output

    async def tagged_query(
        self, query: str, tag: str, k: int = 5, query_type: str = "auto"
    ) -> List[Dict[str, Any]]:
        import faiss
        from backend.knowledge_forest.retrieval import KnowledgeRetrieval

        t0 = time.time()
        logger.info(f"[知识森林] 标签过滤检索: tag={tag} query='{query[:80]}'")

        if not self.is_built:
            if not self.try_load():
                await self.build_forest()

        query_vec = self.embedding.encode(query).astype(np.float32)

        retrieval = KnowledgeRetrieval()
        retrieval.documents = self.documents
        retrieval.load_index("./faiss_index.bin")
        results = retrieval.tagged_search(query_vec, self.documents, tag, k=k, query_text=query)

        output = []
        for r in results:
            output.append({
                "content": r.get("content", ""),
                "source": r.get("source", ""),
                "similarity": round(float(r.get("similarity", 0)), 4),
                "layer": r.get("layer", "L0"),
                "level": r.get("level", 0),
                "cluster_id": r.get("cluster_id", 0),
                "chunk_id": r.get("chunk_id", ""),
                "child_ids": r.get("child_ids", []),
                "tag": r.get("tag", tag),
                "dominant_tag": r.get("dominant_tag", ""),
            })

        elapsed_ms = int((time.time() - t0) * 1000)
        _log_json("TAGGED_QUERY_DONE", {
            "query": query[:80],
            "tag": tag,
            "k": k,
            "results": len(output),
            "elapsed_ms": elapsed_ms,
            "top3_similarities": [o["similarity"] for o in output[:3]],
            "top3_sources": [o["source"] for o in output[:3]],
        })

        return output

    async def multi_channel_query(
        self, query: str, profile: Dict[str, Any] = None, k_per_channel: int = 5
    ) -> Dict[str, List[Dict[str, Any]]]:
        t0 = time.time()
        logger.info(f"[知识森林] ====== 多通道检索: '{query[:80]}' ======")

        channels = {
            "template": f"灌溉发展规划 方案框架 章节结构 {query}",
            "domain_knowledge": f"灌溉技术 需水量 灌水定额 渠系 {query}",
            "standard": f"灌溉标准 规范要求 技术指标 {query}",
        }

        results = {}
        for tag, search_query in channels.items():
            try:
                channel_results = await self.tagged_query(search_query, tag, k=k_per_channel)
                results[tag] = channel_results
                logger.info(f"[多通道检索] {tag}: 返回 {len(channel_results)} 条结果")
                if channel_results:
                    logger.info(f"[多通道检索] {tag} 样本: {channel_results[0].get('source', 'N/A')}")
            except Exception as e:
                import traceback
                logger.warning(f"[多通道检索] {tag}通道失败: {e}\n{traceback.format_exc()}")
                results[tag] = []

        elapsed_ms = int((time.time() - t0) * 1000)
        _log_json("MULTI_CHANNEL_DONE", {
            "query": query[:80],
            "channels": {tag: len(results[tag]) for tag in results},
            "total_results": sum(len(v) for v in results.values()),
            "elapsed_ms": elapsed_ms,
        })

        return results

    def get_status(self) -> Dict[str, Any]:
        from backend.src.models import KnowledgeNode
        db_count = self.db.query(KnowledgeNode).count()
        l0 = len(self.hierarchy.get("L0", []))
        l1 = len(self.hierarchy.get("L1", []))
        l2 = len(self.hierarchy.get("L2", []))
        return {
            "is_built": self.is_built,
            "L0_chunks": l0,
            "L1_summaries": l1,
            "L2_summaries": l2,
            "total_nodes": len(self.documents),
            "db_document_count": db_count,
            "clusters": len(self.cluster_ids),
            "knowledge_base_path": self.knowledge_base_path,
        }

    def get_doc_overview(self) -> Dict[str, Any]:
        from collections import Counter
        source_counter = Counter()
        for doc in self.documents:
            source = doc.get("source", "")
            if not source or source == "unknown":
                continue
            if "," in source:
                continue
            source_counter[source] += 1

        doc_list = []
        for source, count in source_counter.most_common():
            l0 = sum(1 for d in self.documents if d.get("source") == source and d.get("level") == 0)
            l1 = sum(1 for d in self.documents if d.get("source") == source and d.get("level") == 1)
            l2 = sum(1 for d in self.documents if d.get("source") == source and d.get("level") >= 2)
            tag = self._classify_tag(source)
            doc_list.append({
                "source": source,
                "total_nodes": count,
                "L0_chunks": l0,
                "L1_summaries": l1,
                "L2_summaries": l2,
                "tag": tag,
                "tag_label": get_tag_label(tag),
            })

        return {
            "is_built": self.is_built,
            "total_sources": len(doc_list),
            "total_nodes": len(self.documents),
            "L0_total": len(self.hierarchy.get("L0", [])),
            "L1_total": len(self.hierarchy.get("L1", [])),
            "L2_total": len(self.hierarchy.get("L2", [])),
            "clusters": len(self.cluster_ids),
            "documents": doc_list,
        }

    def _load_documents(self):
        from backend.knowledge_forest.parser import DocumentParser
        parser = DocumentParser()
        docs = []
        if not os.path.exists(self.knowledge_base_path):
            os.makedirs(self.knowledge_base_path, exist_ok=True)
            return docs
        for fname in sorted(os.listdir(self.knowledge_base_path)):
            fpath = os.path.join(self.knowledge_base_path, fname)
            if os.path.isfile(fpath):
                logger.info(f"[知识森林] 解析: {fname}")
                try:
                    parsed = parser.parse_document(fpath)
                    docs.extend(parsed)
                except Exception as e:
                    logger.error(f"[知识森林] 解析失败 {fname}: {e}")
        return docs

    def _classify_tag(self, source: str) -> str:
        if hasattr(self, "source_tag_map") and source in self.source_tag_map:
            return self.source_tag_map[source]
        return classify_document_tag(source, "")

    def _group_chunks_by_source(self, chunks: list) -> Dict[str, list]:
        groups = {}
        for chunk in chunks:
            source = chunk.get("source", "unknown")
            if source not in groups:
                groups[source] = []
            groups[source].append(chunk)
        return groups