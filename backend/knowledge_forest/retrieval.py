"""
知识检索模块
使用策略模式重构的检索类，消除代码重复，提升可维护性。
"""
import numpy as np
import faiss
import logging
from typing import List, Dict, Any, Optional

from backend.knowledge_forest.strategies import (
    QueryStrategyFactory,
    DefaultTagFilter,
    TagFilter,
)
from backend.knowledge_forest.processors import (
    CandidateProcessor,
    ResultProcessor,
    SearchStatsLogger,
)

logger = logging.getLogger(__name__)


def _log_json(tag: str, data: dict):
    import json
    logger.info(f"[检索] [{tag}] >>>\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}")


class KnowledgeRetrieval:
    """知识检索类（重构版）
    
    使用策略模式分离查询意图识别、权重计算、标签过滤等职责。
    """
    
    def __init__(self, tag_filter: Optional[TagFilter] = None):
        self.index = None
        self.documents = []
        self.strategy_factory = QueryStrategyFactory()
        self.tag_filter = tag_filter or DefaultTagFilter()

    def build_index(self, embeddings: np.ndarray):
        dim = embeddings.shape[1]
        self.index = faiss.IndexFlatL2(dim)
        self.index.add(embeddings.astype(np.float32))
        logger.info(f"[检索] FAISS索引创建: {embeddings.shape[0]}条, 维度{dim}")

    def save_index(self, filepath: str):
        if self.index is not None:
            faiss.write_index(self.index, filepath)
            logger.info(f"[检索] 索引已保存: {filepath}")

    def load_index(self, filepath: str):
        try:
            self.index = faiss.read_index(filepath)
            logger.info(f"[检索] 索引已加载: {filepath}, 共 {self.index.ntotal}条")
        except Exception as e:
            logger.error(f"[检索] 加载索引失败 {filepath}: {e}")
            self.index = None

    def _search_flat(self, query_vec: np.ndarray, k: int) -> List[tuple]:
        """扁平化向量检索"""
        if self.index is None or self.index.ntotal == 0:
            return []
        query_vec = query_vec.reshape(1, -1).astype(np.float32)
        distances, indices = self.index.search(query_vec, min(k, self.index.ntotal))
        return list(zip(indices[0], distances[0]))

    def hierarchical_search(
        self,
        query_vec: np.ndarray,
        documents: List[Dict],
        k: int = 5,
        query_text: str = "",
    ) -> List[Dict[str, Any]]:
        """层级检索：根据查询意图自动分配权重
        
        流程：
        1. 识别查询类型（细节/宏观/混合）
        2. 应用对应权重策略
        3. 执行向量检索
        4. 处理候选并按层级筛选
        5. 选择并返回结果
        """
        if not documents:
            return []

        # 1. 查询意图识别
        query_type, strategy = self.strategy_factory.classify_query(query_text)
        layer_weights = strategy.get_layer_weights()
        logger.info(
            f"[Ψ-RAG] 查询类型={query_type}, "
            f"层级权重=L0:{layer_weights[0]}/L1:{layer_weights[1]}/L2:{layer_weights[2]}"
        )

        # 2. 基础向量检索
        search_k = max(k * 5, 30)
        flat_results = self._search_flat(query_vec, search_k)

        # 3. 处理候选文档
        candidate_proc = CandidateProcessor(strategy)
        level_candidates = candidate_proc.process_candidates(
            flat_results, documents, k
        )

        # 4. 选择最终结果
        result_proc = ResultProcessor(strategy)
        results = result_proc.select_results(level_candidates, documents, k)

        # 5. 记录统计信息
        SearchStatsLogger.log_hierarchical_search(results, query_type, k)

        return results[:k]

    def tagged_search(
        self,
        query_vec: np.ndarray,
        documents: List[Dict],
        tag: str,
        k: int = 5,
        query_text: str = "",
    ) -> List[Dict[str, Any]]:
        """标签过滤检索：先过滤标签，再按层级检索
        
        流程：
        1. 识别查询意图
        2. 执行向量检索
        3. 应用标签过滤
        4. 处理匹配候选
        5. 无匹配时降级为普通检索
        """
        if not documents:
            return []

        # 1. 查询意图识别
        query_type, strategy = self.strategy_factory.classify_query(query_text)

        # 2. 基础向量检索（更大范围）
        search_k = max(k * 8, 50)
        flat_results = self._search_flat(query_vec, search_k)

        # 3. 统计标签分布
        has_tag_docs = 0
        no_tag_docs = 0
        for global_idx, _ in flat_results:
            if global_idx < len(documents):
                doc = documents[global_idx]
                if doc.get("tag") or doc.get("dominant_tag"):
                    has_tag_docs += 1
                else:
                    no_tag_docs += 1

        # 4. 处理候选文档（带标签过滤）
        candidate_proc = CandidateProcessor(strategy, self.tag_filter)
        level_candidates = candidate_proc.process_candidates(
            flat_results, documents, k, tag=tag, is_tagged_search=True
        )

        # 5. 检查是否有匹配结果
        total_candidates = candidate_proc.count_total_candidates(level_candidates)
        logger.info(
            f"[标签检索] tag={tag} 有标签文档={has_tag_docs}, 无标签文档={no_tag_docs}, "
            f"候选数={total_candidates}"
        )
        
        if total_candidates == 0:
            logger.info(f"[标签检索] tag={tag} 无匹配结果，使用无过滤检索")
            return self.hierarchical_search(query_vec, documents, k=k, query_text=query_text)

        # 6. 选择最终结果
        result_proc = ResultProcessor(strategy)
        results = result_proc.select_results(level_candidates, documents, k)

        # 7. 记录统计信息
        SearchStatsLogger.log_tagged_search(results, tag, k, has_tag_docs, no_tag_docs)

        return results[:k]
