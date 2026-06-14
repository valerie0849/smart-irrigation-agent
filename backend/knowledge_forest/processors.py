"""
检索处理器模块
统一的候选文档处理和结果选择逻辑。
"""
import logging
from typing import Dict, List, Any, Optional

from backend.knowledge_forest.strategies import QueryStrategy, TagFilter

logger = logging.getLogger(__name__)


# ========== 候选文档处理器 ==========

class CandidateProcessor:
    """候选文档处理器：统一处理扁平检索结果"""
    
    def __init__(self, strategy: QueryStrategy, tag_filter: Optional[TagFilter] = None):
        self.strategy = strategy
        self.tag_filter = tag_filter
    
    def process_candidates(
        self,
        flat_results: List[tuple],
        documents: List[Dict],
        k: int,
        tag: Optional[str] = None,
        is_tagged_search: bool = False,
    ) -> Dict[int, List[Dict]]:
        """
        处理候选文档，按层级分组。
        
        Args:
            flat_results: (index, distance) 元组列表
            documents: 文档列表
            k: 目标返回数量
            tag: 标签过滤通道
            is_tagged_search: 是否为标签检索（影响权重计算）
        
        Returns:
            {level: [candidates]} 字典
        """
        layer_weights = self.strategy.get_layer_weights()
        level_candidates = {0: [], 1: [], 2: []}
        
        for global_idx, dist in flat_results:
            if global_idx >= len(documents):
                continue
            
            doc = documents[global_idx]
            level = doc.get("level", 0)
            
            # 标签过滤
            if self.tag_filter and tag and level in (0, 1, 2):
                if not self.tag_filter.matches(doc, tag):
                    continue
            
            if level in (0, 1, 2):
                similarity = float(1.0 / (1.0 + dist))
                weighted_sim = similarity * layer_weights.get(level, 0.3)
                
                # 标签检索时增强权重
                if is_tagged_search and level >= 1:
                    weighted_sim *= 1.2
                    weighted_sim *= 1.3  # 高层级额外增强
                
                level_candidates[level].append({
                    "idx": global_idx,
                    "similarity": similarity,
                    "weighted_similarity": weighted_sim,
                    "layer": f"L{level}",
                    "level": level,
                })
        
        return level_candidates
    
    def count_total_candidates(self, level_candidates: Dict[int, List[Dict]]) -> int:
        """统计候选总数"""
        return sum(len(candidates) for candidates in level_candidates.values())


# ========== 结果处理器 ==========

class ResultProcessor:
    """结果处理器：从候选中选择最终结果"""
    
    def __init__(self, strategy: QueryStrategy):
        self.strategy = strategy
    
    def select_results(
        self,
        level_candidates: Dict[int, List[Dict]],
        documents: List[Dict],
        k: int,
    ) -> List[Dict]:
        """
        从候选文档中选择最终结果。
        
        处理逻辑：
        1. 按层级权重计算每层应选取的数量
        2. 按 L2 -> L1 -> L0 顺序优先选取
        3. 结果不足时补充其他候选
        4. 最终按相似度排序
        """
        layer_weights = self.strategy.get_layer_weights()
        
        # 计算每层应选取的数量
        per_layer_k = {
            0: max(1, int(k * layer_weights[0])),
            1: max(1, int(k * layer_weights[1])),
            2: max(0, int(k * layer_weights[2])),
        }
        
        results = []
        seen_indices = set()
        
        # 按层级优先选取（L2 -> L1 -> L0）
        for level in [2, 1, 0]:
            candidates = level_candidates[level]
            candidates.sort(key=lambda e: e["weighted_similarity"], reverse=True)
            
            take = min(per_layer_k[level], len(candidates))
            for entry in candidates[:take]:
                if entry["idx"] not in seen_indices:
                    seen_indices.add(entry["idx"])
                    doc = self._enrich_document(documents[entry["idx"]], entry)
                    results.append(doc)
        
        # 结果不足时补充
        if len(results) < k:
            results = self._fill_remaining(
                level_candidates, documents, results, seen_indices, k
            )
        
        # 按相似度排序
        results.sort(key=lambda r: r.get("similarity", 0), reverse=True)
        
        return results[:k]
    
    def _enrich_document(self, doc: Dict, entry: Dict) -> Dict:
        """丰富文档信息：添加相似度、层级等元数据"""
        enriched = dict(doc)
        enriched["similarity"] = entry["weighted_similarity"]
        enriched["layer"] = entry["layer"]
        
        # L0层级使用原始内容
        if doc.get("level", 0) == 0:
            raw = doc.get("raw_content", "")
            if raw:
                enriched["content"] = raw
        
        return enriched
    
    def _fill_remaining(
        self,
        level_candidates: Dict[int, List[Dict]],
        documents: List[Dict],
        results: List[Dict],
        seen_indices: set,
        k: int,
    ) -> List[Dict]:
        """填充剩余结果：按 L0 -> L1 -> L2 顺序补充"""
        for level in [0, 1, 2]:
            for entry in level_candidates[level]:
                if len(results) >= k:
                    break
                if entry["idx"] not in seen_indices:
                    seen_indices.add(entry["idx"])
                    doc = self._enrich_document(documents[entry["idx"]], entry)
                    results.append(doc)
        
        return results


# ========== 统计工具 ==========

class SearchStatsLogger:
    """检索统计日志工具"""
    
    @staticmethod
    def log_hierarchical_search(results: List[Dict], query_type: str, k: int):
        """记录层级检索统计"""
        l0_count = sum(1 for r in results if r.get("level") == 0)
        l1_count = sum(1 for r in results if r.get("level") == 1)
        l2_count = sum(1 for r in results if r.get("level") >= 2)
        
        logger.info(
            f"[Ψ-RAG] 混合检索返回{len(results[:k])}条 "
            f"(L0={l0_count}/L1={l1_count}/L2={l2_count}, query_type={query_type})"
        )
    
    @staticmethod
    def log_tagged_search(results: List[Dict], tag: str, k: int, has_tag_docs: int = 0, no_tag_docs: int = 0):
        """记录标签检索统计"""
        l0_count = sum(1 for r in results if r.get("level") == 0)
        l1_count = sum(1 for r in results if r.get("level") == 1)
        l2_count = sum(1 for r in results if r.get("level") >= 2)
        
        logger.info(
            f"[标签检索] tag={tag} 有标签文档={has_tag_docs}, 无标签文档={no_tag_docs}, "
            f"返回{len(results[:k])}条 (L0={l0_count}/L1={l1_count}/L2={l2_count})"
        )
