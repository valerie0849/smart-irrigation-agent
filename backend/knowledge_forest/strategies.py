"""
检索策略模块
定义查询意图识别、权重计算、标签过滤等策略。
"""
import re
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


# ========== 查询策略基类 ==========

class QueryStrategy(ABC):
    """查询策略基类"""
    
    @abstractmethod
    def classify(self, query: str) -> str:
        """分类查询类型"""
        pass
    
    @abstractmethod
    def get_layer_weights(self) -> Dict[int, float]:
        """获取层级权重配置"""
        pass


# ========== 细节查询策略 ==========

class DetailQueryStrategy(QueryStrategy):
    """细节查询策略：关注具体参数、数值、指标"""
    
    def __init__(self):
        self.keywords = [
            # 中文关键词
            "多少", "多大", "哪里", "多少天", "哪些", "哪一年",
            "几倍", "倍数", "比例", "率", "参数", "数值", "指标", "阈值",
            # 英文关键词
            "how much", "how many", "what is the", "specify",
            "parameter", "rate", "ratio", "percentage", "threshold",
        ]
    
    def classify(self, query: str) -> str:
        query_lower = query.lower()
        if any(kw in query_lower for kw in self.keywords):
            return "detail"
        return "unknown"
    
    def get_layer_weights(self) -> Dict[int, float]:
        return {0: 0.60, 1: 0.30, 2: 0.10}


# ========== 宏观查询策略 ==========

class MacroQueryStrategy(QueryStrategy):
    """宏观查询策略：关注趋势、框架、综述"""
    
    def __init__(self):
        self.keywords = [
            # 中文关键词
            "概述", "综述", "态势", "趋势", "技术路线", "发展",
            "体系", "框架", "架构", "方案", "对策", "建议",
            "前景", "展望", "对比", "比较", "总结", "概况",
            # 英文关键词
            "overview", "survey", "trend", "review", "framework",
            "landscape", "comparison", "summary", "introduction",
        ]
    
    def classify(self, query: str) -> str:
        query_lower = query.lower()
        if any(kw in query_lower for kw in self.keywords):
            return "macro"
        return "unknown"
    
    def get_layer_weights(self) -> Dict[int, float]:
        return {0: 0.15, 1: 0.35, 2: 0.50}


# ========== 混合查询策略（默认） ==========

class MixedQueryStrategy(QueryStrategy):
    """混合查询策略：默认权重分配"""
    
    def classify(self, query: str) -> str:
        return "mixed"
    
    def get_layer_weights(self) -> Dict[int, float]:
        return {0: 0.40, 1: 0.35, 2: 0.25}


# ========== 查询策略工厂 ==========

class QueryStrategyFactory:
    """查询策略工厂：根据查询内容自动选择策略"""
    
    def __init__(self):
        self.strategies: List[QueryStrategy] = [
            DetailQueryStrategy(),
            MacroQueryStrategy()
        ]
    
    def classify_query(self, query: str) -> tuple:
        """分类查询并返回（查询类型，对应策略）"""
        for strategy in self.strategies:
            query_type = strategy.classify(query)
            if query_type != "unknown":
                return query_type, strategy
        
        # 默认返回混合策略
        return "mixed", MixedQueryStrategy()


# ========== 标签过滤策略基类 ==========

class TagFilter(ABC):
    """标签过滤策略基类"""
    
    @abstractmethod
    def matches(self, doc: Dict, channel: str) -> bool:
        """判断文档是否匹配标签"""
        pass


# ========== 默认标签过滤器 ==========

class DefaultTagFilter(TagFilter):
    """默认标签过滤策略"""
    
    def __init__(self):
        self.tag_channel_map = {
            "template": "template",
            "standard": "standard",
            "domain_knowledge": "domain_knowledge",
            "case_study": "domain_knowledge",
        }
    
    def matches(self, doc: Dict, channel: str) -> bool:
        level = doc.get("level", 0)
        doc_tag = doc.get("tag", "")
        dominant_tag = doc.get("dominant_tag", "")
        
        mapped_channel = self.tag_channel_map.get(channel, channel)
        
        if level == 2 and dominant_tag:
            return dominant_tag == mapped_channel
        elif level >= 2 and doc_tag:
            return doc_tag == mapped_channel
        elif doc_tag:
            return doc_tag == mapped_channel
        
        # 无标签文档默认匹配
        return True


# ========== 宽松标签过滤器 ==========

class LenientTagFilter(TagFilter):
    """宽松标签过滤：允许领域知识作为兜底"""
    
    def __init__(self):
        self.tag_channel_map = {
            "template": "template",
            "standard": "standard",
            "domain_knowledge": "domain_knowledge",
            "case_study": "domain_knowledge",
        }
    
    def matches(self, doc: Dict, channel: str) -> bool:
        level = doc.get("level", 0)
        doc_tag = doc.get("tag", "")
        dominant_tag = doc.get("dominant_tag", "")
        
        mapped_channel = self.tag_channel_map.get(channel, channel)
        
        # 领域知识通道允许所有domain_knowledge标签
        if mapped_channel == "domain_knowledge":
            if doc_tag in ("domain_knowledge", "case_study"):
                return True
            if dominant_tag == "domain_knowledge":
                return True
        
        # 其他通道使用严格匹配
        if level == 2 and dominant_tag:
            return dominant_tag == mapped_channel
        elif level >= 2 and doc_tag:
            return doc_tag == mapped_channel
        elif doc_tag:
            return doc_tag == mapped_channel
        
        return True
