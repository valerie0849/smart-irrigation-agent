from typing import List, Dict, Any, Optional
import logging
import os
import json
import time
import numpy as np

logger = logging.getLogger(__name__)


def _log_json(tag: str, data: dict):
    logger.info(f"[{tag}] >>>\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}")


class SummaryGenerator:
    def __init__(self):
        self._executor = None

    def _llm_chat(self, prompt: str, max_tokens: int = 256, temperature: float = 0.3) -> str:
        from backend.llm.deepseek import deepseek

        if not deepseek.load():
            logger.error("[LLM] DeepSeek API 未加载")
            return ""

        t0 = time.time()
        try:
            response = deepseek.generate(prompt, max_tokens)
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.info(
                f"[LLM] 推理完成: 耗时={elapsed_ms}ms 输出长度={len(response)}"
            )
            return response.strip()
        except Exception as e:
            logger.error(f"[LLM] 推理异常: {e}")
            return ""

    def _lookup_context(self, chunk: Dict, all_chunks: Dict[str, Dict]) -> tuple:
        prev_id = chunk.get("prev_chunk_id", "")
        next_id = chunk.get("next_chunk_id", "")
        prev_text = all_chunks.get(prev_id, {}).get("raw_content", "") if prev_id else ""
        next_text = all_chunks.get(next_id, {}).get("raw_content", "") if next_id else ""
        return prev_text, next_text

    def _build_context_prompt(
        self,
        chunks: List[Dict],
        all_chunks: Dict[str, Dict],
        max_length: int = 300,
        max_input_chars: int = 4500,
    ) -> str:
        n = len(chunks)
        if n <= 5:
            selected = chunks
        elif n <= 10:
            selected = sorted(chunks, key=lambda c: c.get("_sim", 0), reverse=True)[:5]
        else:
            import random
            random.seed(42)
            selected = random.sample(chunks, min(5, n))

        parts = []
        total_chars = 0
        for c in selected:
            prev_text, next_text = self._lookup_context(c, all_chunks)
            current = c.get("content", "")
            if prev_text and next_text:
                block = f"[前段]\n{prev_text}\n\n[当前段]\n{current}\n\n[后段]\n{next_text}"
            elif prev_text:
                block = f"[前段]\n{prev_text}\n\n[当前段]\n{current}"
            elif next_text:
                block = f"[当前段]\n{current}\n\n[后段]\n{next_text}"
            else:
                block = current

            if total_chars + len(block) > max_input_chars:
                block = block[:max_input_chars - total_chars] + "..."
                parts.append(block)
                break
            parts.append(block)
            total_chars += len(block) + 4

        combined = "\n\n---\n\n".join(parts)

        prompt = f"""请为以下灌溉领域文本生成一段简洁摘要，要求：
1. 长度控制在{max_length}字左右
2. 保留核心知识点、技术参数和关键结论
3. 语言精炼专业
4. 每个文本块包含[前段][当前段][后段]，请利用前后段理解上下文，但摘要聚焦当前段核心内容

文本内容：
{combined}

请直接输出摘要，不要任何前缀说明："""
        return prompt

    def _llm_summarize(
        self,
        chunks: List[Dict],
        all_chunks: Optional[Dict[str, Dict]] = None,
        max_length: int = 300,
    ) -> str:
        if all_chunks is None:
            all_chunks = {c.get("chunk_id", ""): c for c in chunks}

        prompt = self._build_context_prompt(chunks, all_chunks, max_length)
        summary = self._llm_chat(prompt, max_tokens=400, temperature=0.3)
        if not summary:
            logger.warning("[Summary] LLM返回空, 回退规则摘要")
            texts = [c.get("content", "") for c in chunks]
            return self._rule_based_summary(texts, max_length)
        return summary

    def _rule_based_summary(self, texts: List[str], max_length: int = 300) -> str:
        combined = " ".join(texts)
        if len(combined) <= max_length:
            return combined
        sentences = self._split_sentences(combined)
        result = []
        length = 0
        for s in sentences:
            if length + len(s) <= max_length:
                result.append(s)
                length += len(s) + 1
            else:
                break
        return "".join(result) + ("..." if result else "")

    def _split_sentences(self, text: str) -> List[str]:
        import re
        sentences = re.split(r'(?<=[。！？；.!?])', text)
        return [s.strip() for s in sentences if s.strip()]

    def _umap_reduce(self, embeddings: np.ndarray, source_name: str = "") -> np.ndarray:
        n_samples = embeddings.shape[0]
        if n_samples < 4:
            _log_json("UMAP_SKIP", {"source": source_name, "reason": "n_samples<4", "n": n_samples})
            return embeddings

        try:
            import umap

            n_neighbors = min(5, n_samples - 1)
            target_dim = max(2, min(8, n_samples // 2, embeddings.shape[1] // 4))
            reducer = umap.UMAP(
                n_neighbors=n_neighbors,
                n_components=target_dim,
                min_dist=0.1,
                metric='cosine',
                random_state=42,
            )
            reduced = reducer.fit_transform(embeddings)
            _log_json("UMAP_REDUCE", {
                "source": source_name,
                "input_shape": list(embeddings.shape),
                "output_shape": list(reduced.shape),
                "n_neighbors": n_neighbors,
                "target_dim": target_dim,
            })
            return reduced
        except ImportError:
            logger.warning(f"[UMAP] umap-learn未安装,跳过降维 [{source_name}]")
            return embeddings
        except Exception as e:
            logger.warning(f"[UMAP] 降维失败 [{source_name}]: {e}")
            return embeddings

    def per_document_summarize(
        self,
        doc_chunks: List[Dict[str, Any]],
        doc_embeddings: np.ndarray,
        source_name: str,
    ) -> List[Dict[str, Any]]:
        from backend.knowledge_forest.clustering import TextClustering

        t_start = time.time()
        n_chunks = len(doc_chunks)
        all_chunks_map = {c.get("chunk_id", ""): c for c in doc_chunks}
        _log_json("DOC_START", {
            "source": source_name,
            "chunk_count": n_chunks,
            "emb_shape": list(doc_embeddings.shape),
        })

        if n_chunks <= 3:
            summary_text = self._llm_summarize(doc_chunks, all_chunks_map, max_length=300)
            chunk_ids = [c.get("chunk_id", "") for c in doc_chunks]
            elapsed = round(time.time() - t_start, 2)
            result = [{
                "level": 1,
                "cluster_id": 0,
                "content": summary_text,
                "child_ids": chunk_ids,
                "source": source_name,
                "metadata": {
                    "document_count": 1,
                    "sources": [source_name],
                },
            }]
            _log_json("DOC_DONE", {
                "source": source_name,
                "strategy": "direct_summary",
                "summary_count": len(result),
                "elapsed_sec": elapsed,
            })
            return result

        reduced_embs = self._umap_reduce(doc_embeddings, source_name)

        clustering = TextClustering(max_clusters=5, min_cluster_size=3)
        clustered_chunks = clustering.cluster_documents(doc_chunks, reduced_embs, method="gmm")

        clusters = {}
        for chunk in clustered_chunks:
            cid = chunk.get("cluster_id", 0)
            if cid not in clusters:
                clusters[cid] = []
            clusters[cid].append(chunk)

        _log_json("DOC_CLUSTER", {
            "source": source_name,
            "total_clusters": len(clusters),
            "cluster_sizes": {str(k): len(v) for k, v in clusters.items()},
            "cluster_ids": sorted(clusters.keys()),
        })

        summaries = []
        for cid, chunks in clusters.items():
            t_s = time.time()
            summary_text = self._llm_summarize(chunks, all_chunks_map, max_length=300)
            elapsed_s = round(time.time() - t_s, 2)
            summaries.append({
                "level": 1,
                "cluster_id": cid,
                "content": summary_text,
                "child_ids": [c.get("chunk_id", "") for c in chunks],
                "source": source_name,
                "metadata": {
                    "document_count": 1,
                    "sources": [source_name],
                },
            })
            logger.info(f"[Summary] [{source_name}] cluster_{cid} 摘要生成: {elapsed_s}s, {len(summary_text)}字")

        total_elapsed = round(time.time() - t_start, 2)
        _log_json("DOC_DONE", {
            "source": source_name,
            "strategy": "umap_gmm_context_summary",
            "chunks_in": n_chunks,
            "clusters_found": len(clusters),
            "summaries_out": len(summaries),
            "elapsed_sec": total_elapsed,
        })
        return summaries

    def generate_cross_document_summary(
        self,
        per_doc_summaries: List[Dict[str, Any]],
        embeddings: np.ndarray,
        level: int = 2,
    ) -> List[Dict[str, Any]]:
        from backend.knowledge_forest.clustering import TextClustering

        t_start = time.time()
        n_input = len(per_doc_summaries)
        sources = list(set(s.get("source", "") for s in per_doc_summaries))
        _log_json("CROSS_START", {
            "level": level,
            "summary_count": n_input,
            "source_count": len(sources),
            "sources": sources,
            "emb_shape": list(embeddings.shape),
        })

        if n_input <= 3:
            texts = [s.get("content", "") for s in per_doc_summaries]
            combined = "\n\n".join(texts)
            summary_text = self._llm_chat(
                f"请为以下灌溉领域文本生成跨文档主题摘要(400字左右):\n\n{combined}\n\n请直接输出摘要：",
                max_tokens=400,
            )
            summary_text = summary_text.strip() if summary_text else combined[:400]
            child_ids = [s.get("content", "")[:50] for s in per_doc_summaries]
            elapsed = round(time.time() - t_start, 2)
            source_names = ", ".join(sources[:3])
            if len(sources) > 3:
                source_names += f" 等{len(sources)}篇"
            result = [{
                "level": level,
                "cluster_id": 0,
                "content": summary_text,
                "child_ids": child_ids,
                "source": source_names,
                "metadata": {
                    "document_count": len(sources),
                    "sources": sources,
                },
            }]
            _log_json("CROSS_DONE", {
                "level": level,
                "strategy": "direct_merge",
                "summary_count": len(result),
                "elapsed_sec": elapsed,
            })
            return result

        clustering = TextClustering()
        clustered = clustering.cluster_documents(
            per_doc_summaries, embeddings, method="gmm"
        )

        cross_clusters = {}
        for s in clustered:
            cid = s.get("cluster_id", 0)
            if cid not in cross_clusters:
                cross_clusters[cid] = []
            cross_clusters[cid].append(s)

        _log_json("CROSS_CLUSTER", {
            "level": level,
            "total_clusters": len(cross_clusters),
            "cluster_sizes": {str(k): len(v) for k, v in cross_clusters.items()},
        })

        summaries = []
        for cid, docs in cross_clusters.items():
            t_s = time.time()
            texts = [d.get("content", "") for d in docs]
            combined = "\n\n".join(texts)
            summary_text = self._llm_chat(
                f"请为以下灌溉领域文本生成跨文档主题摘要(400字左右):\n\n{combined}\n\n请直接输出摘要：",
                max_tokens=400,
            )
            summary_text = summary_text.strip() if summary_text else combined[:400]

            child_ids = []
            for d in docs:
                child_ids.extend(d.get("child_ids", []))

            doc_sources = list(set(d.get("source", "") for d in docs))
            source_names = ", ".join(doc_sources[:3])
            if len(doc_sources) > 3:
                source_names += f" 等{len(doc_sources)}篇"

            summaries.append({
                "level": level,
                "cluster_id": cid,
                "content": summary_text,
                "child_ids": child_ids,
                "source": source_names,
                "metadata": {
                    "document_count": len(doc_sources),
                    "sources": doc_sources,
                },
            })
            elapsed_s = round(time.time() - t_s, 2)
            logger.info(f"[Summary] L{level} cluster_{cid} 跨文档摘要: {elapsed_s}s, {len(summary_text)}字, {len(docs)}个输入")

        total_elapsed = round(time.time() - t_start, 2)
        _log_json("CROSS_DONE", {
            "level": level,
            "input_summaries": n_input,
            "output_summaries": len(summaries),
            "clusters_found": len(cross_clusters),
            "elapsed_sec": total_elapsed,
        })
        return summaries