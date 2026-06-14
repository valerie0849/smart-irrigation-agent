"""
知识森林构建管道模块
将 build_forest() 方法拆分为独立的管道阶段，遵循单一职责原则。
"""
import os
import json
import pickle
import time
import logging
import traceback
from abc import ABC, abstractmethod
from typing import List, Dict, Set, Any, Optional

import numpy as np
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from backend.knowledge_forest.tags import (
    classify_document_tag,
    compute_cluster_tag_ratios,
    get_tag_label,
    TAG_DEFINITIONS,
)


def _log_json(tag: str, data: dict):
    logger.info(f"[{tag}] >>>\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}")


# ========== 上下文对象 ==========

class ForestBuildContext:
    """知识森林构建上下文，统一管理构建过程中的所有状态和数据"""

    def __init__(self, db: Session, knowledge_base_path: str):
        self.db = db
        self.knowledge_base_path = knowledge_base_path
        self.build_completed = True

        # 输入数据
        self.docs: List[Dict] = []
        self.source_list: List[str] = []
        self.source_tag_map: Dict[str, str] = {}

        # 嵌入模型引用
        self.embedding: Any = None

        # 中间数据
        self.chunks: List[Dict] = []
        self.l1_summaries: List[Dict] = []
        self.l2_summaries: List[Dict] = []

        # 输出数据
        self.all_nodes: List[Dict] = []
        self.cluster_ids: Set[int] = set()
        self.hierarchy: Dict[str, List[Dict]] = {"L0": [], "L1": [], "L2": []}


# ========== 管道阶段基类 ==========

class PipelineStage(ABC):
    """管道处理阶段基类"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        """执行处理阶段，接收上下文并返回更新后的上下文"""
        pass


# ========== 阶段1: 文档加载 ==========

class DocumentLoadStage(PipelineStage):
    """文档加载阶段：从知识库路径加载并解析所有文档"""

    def __init__(self):
        super().__init__("DocumentLoad")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        from backend.knowledge_forest.parser import DocumentParser

        t0 = time.time()
        parser = DocumentParser()
        docs = []

        if not os.path.exists(context.knowledge_base_path):
            os.makedirs(context.knowledge_base_path, exist_ok=True)
            context.docs = docs
            return context

        for fname in sorted(os.listdir(context.knowledge_base_path)):
            fpath = os.path.join(context.knowledge_base_path, fname)
            if os.path.isfile(fpath):
                logger.info(f"[知识森林] 解析: {fname}")
                try:
                    parsed = parser.parse_document(fpath)
                    docs.extend(parsed)
                except Exception as e:
                    logger.error(f"[知识森林] 解析失败 {fname}: {e}")

        context.docs = docs

        if not docs:
            logger.warning("[知识森林] 知识库为空")
            context.build_completed = False

        context.source_list = sorted(set(d.get("source", "") for d in docs))

        _log_json("STEP0_LOAD", {
            "raw_docs": len(docs),
            "unique_sources": len(context.source_list),
            "sources": context.source_list,
            "elapsed_sec": round(time.time() - t0, 2),
        })

        return context


# ========== 阶段2: 标签分类 ==========

class TagClassificationStage(PipelineStage):
    """标签分类阶段：自动识别文档标签"""

    def __init__(self):
        super().__init__("TagClassification")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        logger.info("[知识森林] ====== 功能标签自动识别 ======")
        t_tag = time.time()

        context.source_tag_map = {}
        for src in context.source_list:
            context.source_tag_map[src] = classify_document_tag(src, "")

        _log_json("STEP0_TAG", {
            "sources": len(context.source_tag_map),
            "tags": {s: t for s, t in context.source_tag_map.items()},
            "distribution": {
                tag: sum(1 for t in context.source_tag_map.values() if t == tag)
                for tag in TAG_DEFINITIONS
            },
            "elapsed_sec": round(time.time() - t_tag, 2),
        })

        return context


# ========== 阶段3: 文本分割 ==========

class TextSplittingStage(PipelineStage):
    """文本分割阶段：将文档切分为文本块"""

    def __init__(self):
        super().__init__("TextSplitting")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        from backend.knowledge_forest.splitter import TextSplitter

        t0 = time.time()
        splitter = TextSplitter()
        chunks = splitter.split_documents(context.docs)

        for chunk in chunks:
            src = chunk.get("source", "")
            chunk["tag"] = context.source_tag_map.get(src, "domain_knowledge")

        context.chunks = chunks

        _log_json("STEP1_SPLIT", {
            "chunks": len(chunks),
            "elapsed_sec": round(time.time() - t0, 2),
        })

        return context


# ========== 阶段4: 向量化 ==========

class VectorizationStage(PipelineStage):
    """向量化阶段：将文本块转换为嵌入向量"""

    def __init__(self):
        super().__init__("Vectorization")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        from backend.knowledge_forest.embedding import TextEmbedding

        t0 = time.time()
        context.embedding = TextEmbedding(model_path="./models")
        texts = [c["content"] for c in context.chunks]
        context.embedding.fit(texts)
        chunk_embs = context.embedding.batch_encode(texts)

        for chunk, emb in zip(context.chunks, chunk_embs):
            chunk["embedding"] = emb

        _log_json("STEP2_VECTORIZE", {
            "chunks": len(context.chunks),
            "embedding_dim": int(chunk_embs.shape[1]),
            "elapsed_sec": round(time.time() - t0, 2),
        })

        return context


# ========== 阶段5: 单文档聚类摘要 ==========

class PerDocClusteringStage(PipelineStage):
    """单文档聚类摘要阶段：在每个文档内进行聚类和摘要生成（并行处理）"""

    def __init__(self):
        super().__init__("PerDocClustering")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        import asyncio
        from backend.knowledge_forest.summary import SummaryGenerator

        for chunk in context.chunks:
            chunk["level"] = 0
        context.hierarchy["L0"] = context.chunks
        logger.info(f"[知识森林] L0层: {len(context.chunks)}个文本块节点")

        logger.info("[知识森林] ====== 阶段一: 单文档内聚类摘要 (并行) ======")
        t0 = time.time()

        # 分组文档块
        doc_chunks_map = self._group_chunks_by_source(context.chunks)
        source_names = sorted(doc_chunks_map.keys())

        _log_json("PHASE1_GROUP", {
            "source_count": len(source_names),
            "sources": source_names,
            "chunks_per_source": {s: len(v) for s, v in doc_chunks_map.items()},
        })

        # 创建并行任务
        summary_gen = SummaryGenerator()
        tasks = [
            self._process_single_doc(summary_gen, source_name, doc_chunks_map[source_name])
            for source_name in source_names
        ]

        # 并行执行
        per_doc_results = await asyncio.gather(*tasks)

        # 聚合结果
        all_per_doc_summaries = []
        phase1_summary = {}
        for i, summaries in enumerate(per_doc_results):
            source_name = source_names[i]
            tag = context.source_tag_map.get(source_name, "domain_knowledge")
            for s in summaries:
                s["source"] = source_name
                s["tag"] = tag
            all_per_doc_summaries.extend(summaries)
            phase1_summary[source_name] = len(summaries)

        _log_json("PHASE1_DONE", {
            "total_docs": len(source_names),
            "per_doc_summaries": phase1_summary,
            "total_l1_summaries": len(all_per_doc_summaries),
            "elapsed_sec": round(time.time() - t0, 2),
        })

        context.l1_summaries = all_per_doc_summaries
        return context

    def _group_chunks_by_source(self, chunks: list) -> Dict[str, list]:
        """按来源分组文档块"""
        groups = {}
        for chunk in chunks:
            source = chunk.get("source", "unknown")
            if source not in groups:
                groups[source] = []
            groups[source].append(chunk)
        return groups

    async def _process_single_doc(self, summary_gen, source_name: str, doc_chunks: list):
        """处理单个文档的聚类摘要"""
        import asyncio
        loop = asyncio.get_event_loop()
        doc_embs = np.array([c["embedding"] for c in doc_chunks], dtype=np.float64)
        return await loop.run_in_executor(
            None,
            summary_gen.per_document_summarize,
            doc_chunks,
            doc_embs,
            source_name,
        )


# ========== 阶段6: 跨文档聚类摘要 ==========

class CrossDocClusteringStage(PipelineStage):
    """跨文档聚类摘要阶段：跨文档聚类并生成高层摘要"""

    def __init__(self):
        super().__init__("CrossDocClustering")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        from backend.knowledge_forest.summary import SummaryGenerator

        logger.info("[知识森林] ====== 阶段二: 跨文档聚类摘要 ======")
        t0 = time.time()
        l3_summaries = []

        all_per_doc_summaries = context.l1_summaries

        if len(all_per_doc_summaries) <= 3:
            context.l1_summaries = all_per_doc_summaries
            context.l2_summaries = []
            _log_json("PHASE2_SKIP", {"reason": "单文档摘要≤3,跳过跨文档聚类"})
        else:
            summary_gen = SummaryGenerator()
            per_doc_texts = [s["content"] for s in all_per_doc_summaries]
            per_doc_embs = context.embedding.batch_encode(per_doc_texts)

            context.l1_summaries = all_per_doc_summaries
            l2_summaries = summary_gen.generate_cross_document_summary(
                all_per_doc_summaries, per_doc_embs, level=2
            )

            if len(l2_summaries) > 5:
                l2_texts = [s["content"] for s in l2_summaries]
                l2_embs = context.embedding.batch_encode(l2_texts)
                l3_summaries = summary_gen.generate_cross_document_summary(
                    l2_summaries, l2_embs, level=3
                )
                l2_summaries.extend(l3_summaries)
                logger.info(f"[知识森林] L3递归摘要: {len(l3_summaries)}个")

            context.l2_summaries = l2_summaries

        _log_json("PHASE2_DONE", {
            "l1_summaries": len(context.l1_summaries),
            "l2_summaries": len(context.l2_summaries) - len(l3_summaries),
            "l3_summaries": len(l3_summaries),
            "elapsed_sec": round(time.time() - t0, 2),
        })

        return context


# ========== 阶段7: 标签传播 ==========

class TagPropagationStage(PipelineStage):
    """标签传播阶段：计算L2摘要的标签比例，为L1/L2摘要生成嵌入向量"""

    def __init__(self):
        super().__init__("TagPropagation")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        t_tag2 = time.time()

        for s in context.l2_summaries:
            cluster_sources = s.get("metadata", {}).get("sources", [])
            tag_ratios = compute_cluster_tag_ratios(
                [{"source": src} for src in cluster_sources],
                context.source_tag_map,
            )
            s["tag_ratios"] = tag_ratios
            dominant = max(tag_ratios, key=tag_ratios.get) if tag_ratios else ""
            s["dominant_tag"] = dominant.replace("_ratio", "") if dominant else "mixed"

        _log_json("STEP_L2_TAG", {
            "l2_clusters": len(context.l2_summaries),
            "tag_ratios": [{
                "cluster_id": s.get("cluster_id", 0),
                "dominant": s.get("dominant_tag", ""),
                "ratios": {k: v for k, v in s.get("tag_ratios", {}).items()},
                "content_preview": s.get("content", "")[:60],
            } for s in context.l2_summaries],
            "elapsed_sec": round(time.time() - t_tag2, 2),
        })

        logger.info("[知识森林] ====== 标签传播完成 L2→L1→L0 ======")

        t0 = time.time()
        for s in context.l1_summaries:
            s["embedding"] = context.embedding.encode(s["content"])
        for s in context.l2_summaries:
            s["embedding"] = context.embedding.encode(s["content"])
        _log_json("STEP3_L1L2_EMBED", {"elapsed_sec": round(time.time() - t0, 2)})

        context.hierarchy["L1"] = context.l1_summaries
        context.hierarchy["L2"] = context.l2_summaries
        logger.info(f"[知识森林] L1摘要: {len(context.l1_summaries)}个, L2/L3摘要: {len(context.l2_summaries)}个")

        return context


# ========== 阶段8: FAISS索引构建 ==========

class IndexBuildStage(PipelineStage):
    """FAISS索引构建阶段：构建向量索引并保存到磁盘"""

    def __init__(self):
        super().__init__("IndexBuild")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        import faiss
        from backend.knowledge_forest.retrieval import KnowledgeRetrieval

        all_summaries = context.l1_summaries + context.l2_summaries
        all_nodes = context.chunks + all_summaries

        context.all_nodes = all_nodes
        context.cluster_ids = set(
            c.get("cluster_id", 0) for c in all_nodes
        )

        logger.info(f"[知识森林] 总节点: {len(all_nodes)} (L0={len(context.chunks)} L1={len(context.l1_summaries)} L2+={len(context.l2_summaries)})")

        t0 = time.time()
        retrieval = KnowledgeRetrieval()
        retrieval.documents = all_nodes
        emb_array = np.array([d["embedding"] for d in all_nodes], dtype=np.float32)
        dim = emb_array.shape[1]
        retrieval.index = faiss.IndexIDMap(faiss.IndexFlatL2(dim))
        ids = np.arange(emb_array.shape[0], dtype=np.int64)
        retrieval.index.add_with_ids(emb_array, ids)
        retrieval.save_index("./faiss_index.bin")
        with open("./faiss_docs.pkl", "wb") as f:
            pickle.dump({
                "documents": all_nodes,
                "hierarchy": context.hierarchy,
                "source_tag_map": context.source_tag_map,
            }, f)

        _log_json("STEP4_FAISS", {
            "total_nodes": len(all_nodes),
            "index_dim": int(dim),
            "elapsed_sec": round(time.time() - t0, 2),
        })

        return context


# ========== 阶段9: 数据库持久化 ==========

class PersistenceStage(PipelineStage):
    """数据库持久化阶段：将构建结果保存到MySQL数据库"""

    def __init__(self):
        super().__init__("Persistence")

    async def execute(self, context: ForestBuildContext) -> ForestBuildContext:
        import importlib
        from backend.src.models import KnowledgeNode
        from datetime import datetime

        t0 = time.time()
        context.db.query(KnowledgeNode).delete()

        for doc in context.all_nodes:
            meta = {
                "source": doc.get("source", ""),
                "page": doc.get("page", 0),
                "cluster_id": doc.get("cluster_id", 0),
                "level": doc.get("level", 0),
                "child_ids": doc.get("child_ids", []),
                "chunk_id": doc.get("chunk_id", ""),
                "raw_content": doc.get("raw_content", "")[:200] if doc.get("raw_content") else "",
                "tag": doc.get("tag", "domain_knowledge"),
            }
            if doc.get("level", 0) >= 2 and doc.get("tag_ratios"):
                meta["tag_ratios"] = doc["tag_ratios"]

            tag = doc.get("tag") or classify_document_tag(doc.get("source", ""), "")
            node = KnowledgeNode(
                level=doc.get("level", 0),
                content=doc.get("content", "")[:5000],
                metadata_info=json.dumps(meta, ensure_ascii=False),
                parent_id=doc.get("parent_id"),
                cluster_id=doc.get("cluster_id", 0),
                tag=tag,
            )
            context.db.add(node)
        context.db.commit()

        from backend.src.models import SystemConfig
        config = context.db.query(SystemConfig).filter(SystemConfig.key == "knowledge_forest_version").first()
        version = str(int(datetime.now().timestamp()))
        if config:
            config.value = version
            config.updated_at = datetime.now()
        else:
            config = SystemConfig(key="knowledge_forest_version", value=version)
            context.db.add(config)
        context.db.commit()

        _log_json("STEP5_MYSQL", {
            "persisted_nodes": len(context.all_nodes),
            "elapsed_sec": round(time.time() - t0, 2),
        })

        return context


# ========== 管道执行器 ==========

class ForestBuildPipeline:
    """知识森林构建管道，协调各阶段的执行"""

    def __init__(self, db: Session, knowledge_base_path: str):
        self.db = db
        self.knowledge_base_path = knowledge_base_path
        self._stages: List[PipelineStage] = []
        self._build_stages()

    def _build_stages(self):
        """构建管道处理阶段"""
        self._stages = [
            DocumentLoadStage(),
            TagClassificationStage(),
            TextSplittingStage(),
            VectorizationStage(),
            PerDocClusteringStage(),
            CrossDocClusteringStage(),
            TagPropagationStage(),
            IndexBuildStage(),
            PersistenceStage(),
        ]

    async def execute(self) -> Dict[str, Any]:
        """执行管道，返回构建结果"""
        self.context = ForestBuildContext(self.db, self.knowledge_base_path)

        forest_start = time.time()
        logger.info("=" * 60)
        logger.info("[知识森林] ========== 两步走聚类摘要构建流程 ==========")

        for stage in self._stages:
            if not self.context.build_completed:
                logger.warning(f"[管道] 构建流程已终止，跳过阶段 {stage.name}")
                break

            try:
                logger.info(f"[管道] 执行阶段: {stage.name}")
                self.context = await stage.execute(self.context)
            except Exception as e:
                logger.error(f"[管道] 阶段 {stage.name} 执行失败: {e}")
                logger.error(f"[管道] 错误详情: {traceback.format_exc()}")
                self.context.build_completed = False
                break

        total_elapsed = round(time.time() - forest_start, 2)

        if self.context.build_completed:
            result = {
                "L0_chunks": len(self.context.chunks),
                "L1_summaries": len(self.context.l1_summaries),
                "L2_summaries": len(self.context.l2_summaries),
                "total_nodes": len(self.context.all_nodes),
                "clusters": len(self.context.cluster_ids),
                "knowledge_base_path": self.knowledge_base_path,
                "total_elapsed_sec": total_elapsed,
            }
            _log_json("FOREST_BUILD_COMPLETE", result)
            return result
        else:
            logger.error(f"[管道] 知识森林构建失败，总耗时: {total_elapsed}秒")
            return {"document_count": 0}