import re
import json
import logging
import time
from typing import Dict, List, Optional
from collections import Counter

logger = logging.getLogger(__name__)


def _log_json(tag: str, data: dict):
    logger.info(f"[标签] [{tag}] >>>\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}")


TAG_DEFINITIONS = {
    "template": {
        "label": "结构模板",
        "description": "政府灌溉方案文件，用于提取方案框架和章节结构",
        "filename_keywords": [
            "规划", "方案", "报批稿", "征求意见稿", "实施方案",
            "行动计划", "工作计划", "总体规划",
        ],
        "content_keywords": [
            "发展目标", "总体布局", "建设任务", "投资估算",
            "保障措施", "指导思想", "基本原则", "重点工程",
            "近期目标", "远期目标",
        ],
    },
    "standard": {
        "label": "合规标尺",
        "description": "国标/行标/团标，用于合规校验和阈值比对",
        "filename_keywords": [
            "GB", "gb", "SL", "sl", "T/CIDA", "T\\CIDA",
            "国标", "行标", "团标", "规范", "标准",
            "技术导则", "技术规程", "评价规范",
        ],
        "content_keywords": [
            "本标准", "规范性引用", "术语和定义",
            "技术要求", "检验规则", "不应", "应",
        ],
    },
    "domain_knowledge": {
        "label": "技术参数",
        "description": "学术论文/技术文献，用于填充技术方法和量化参数",
        "filename_keywords": [
            "研究", "模型", "方法", "算法", "优化",
            "设计", "应用", "技术", "分析", "评价",
            "探讨", "试验", "实验", "模拟", "预测",
            "数字孪生", "物联网", "Docker", "Kubernetes",
            "机器学习", "深度学习", "神经网络",
        ],
        "content_keywords": [
            "实验", "模型", "算法", "数据集", "准确率",
            "结果表明", "本文提出", "研究方法",
            "相关系数", "回归分析", "均方根误差",
        ],
    },
    "case_study": {
        "label": "案例实践",
        "description": "灌区/地区案例分析，用于参照对比",
        "filename_keywords": [
            "灌区", "案例", "实践", "示范", "推广",
            "应用实例", "工程实例", "典型设计",
        ],
        "content_keywords": [
            "灌区概况", "工程案例", "实施效果",
            "运行情况", "经济效益", "社会效益",
        ],
    },
}


def classify_document_tag(source: str, content: str = "") -> str:
    s = source.lower()
    content_lower = content.lower() if content else ""

    scores = {tag: 0 for tag in TAG_DEFINITIONS}

    for tag, config in TAG_DEFINITIONS.items():
        for kw in config["filename_keywords"]:
            if kw.lower() in s:
                scores[tag] += 3

    if content_lower:
        for tag, config in TAG_DEFINITIONS.items():
            for kw in config["content_keywords"]:
                if kw.lower() in content_lower:
                    scores[tag] += 1

    if max(scores.values()) == 0:
        return "domain_knowledge"

    return max(scores, key=scores.get)


def classify_all_documents(
    documents: List[Dict],
    sources: Optional[List[str]] = None,
) -> Dict[str, Dict]:
    t0 = time.time()

    doc_tags = {}
    source_tag_map = {}
    tag_counter = Counter()

    if sources:
        for src in sources:
            tag = classify_document_tag(src, "")
            source_tag_map[src] = tag

    if not source_tag_map and documents:
        for d in documents:
            src = d.get("source", "")
            if src and src not in source_tag_map:
                source_tag_map[src] = classify_document_tag(src, d.get("content", ""))

    for d in documents:
        src = d.get("source", "")
        if src and src in source_tag_map:
            tag = source_tag_map[src]
            doc_tags[d.get("chunk_id", str(id(d)))] = tag
            tag_counter[tag] += 1
        else:
            doc_tags[d.get("chunk_id", str(id(d)))] = "domain_knowledge"
            tag_counter["domain_knowledge"] += 1

    _log_json("CLASSIFY_ALL", {
        "total_documents": len(documents),
        "unique_sources": len(source_tag_map),
        "tag_distribution": dict(tag_counter),
        "source_tags": {s: t for s, t in source_tag_map.items()},
        "elapsed_sec": round(time.time() - t0, 2),
    })

    return {
        "doc_tags": doc_tags,
        "source_tag_map": source_tag_map,
        "tag_counts": dict(tag_counter),
    }


def compute_cluster_tag_ratios(
    cluster_docs: List[Dict],
    source_tag_map: Dict[str, str],
) -> Dict[str, float]:
    if not cluster_docs:
        return {tag: 0.0 for tag in TAG_DEFINITIONS}

    counter = Counter()
    for d in cluster_docs:
        src = d.get("source", "")
        tag = source_tag_map.get(src, "domain_knowledge")
        counter[tag] += 1

    total = max(counter.total(), 1)
    ratios = {}
    for tag in TAG_DEFINITIONS:
        ratios[f"{tag}_ratio"] = round(counter.get(tag, 0) / total, 4)

    return ratios


def get_tag_label(tag: str) -> str:
    return TAG_DEFINITIONS.get(tag, {}).get("label", tag)


def get_tag_description(tag: str) -> str:
    return TAG_DEFINITIONS.get(tag, {}).get("description", tag)


TAG_CHANNEL_MAP = {
    "template": "template",
    "standard": "standard",
    "domain_knowledge": "domain_knowledge",
    "case_study": "domain_knowledge",
}