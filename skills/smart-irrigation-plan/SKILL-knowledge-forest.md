---
name: knowledge-forest
description: >
  知识森林构建与检索技能。将领域PDF文献解析为三层知识森林（L0文本块→L1单文档摘要
  →L2跨文档摘要），构建Faiss向量索引，支持Ψ-RAG分层检索、多通道检索和标签过滤检索。
  支持增量更新和后台热更新。代码位于 backend/knowledge_forest/main.py 和 pipeline.py。
---

# 知识森林技能 — 文档解析、向量化、聚类、检索

## 何时使用

- 系统启动时：`KnowledgeForest(db).try_load()` 尝试从缓存加载
- 知识森林未构建时：`await knowledge_forest.build_forest()` 触发完整构建
- 方案生成流程中：按需检索（`query()`, `tagged_query()`, `multi_channel_query()`）
- 用户请求浏览知识库时：`get_doc_overview()` 获取全貌

代码位置：`backend/knowledge_forest/main.py`（主类）、`backend/knowledge_forest/pipeline.py`（管道模式）

## 领域知识

### 三层知识结构

```
L0（文本块层）
  ├── 文档切分后的原始文本片段
  ├── 由 TextSplitter 切分
  └── 作为检索的最细粒度层

L1（单文档摘要层）
  ├── 每个文档内 GMM 聚类生成的摘要
  ├── 由 SummaryGenerator.per_document_summarize() 生成
  └── 保留文档内语义结构

L2/L3（跨文档摘要层）
  ├── 跨文档 GMM 聚类生成的高层知识
  ├── L2 由 SummaryGenerator.generate_cross_document_summary(level=2) 生成
  ├── L3 在 L2 基础上递归聚类（当 L2 > 5 时，level=3）
  └── 作为最高层知识导航
```

### 管道模式9个阶段

管道定义在 `ForestBuildPipeline` 类（`pipeline.py:506`）：

```python
class ForestBuildPipeline:
    def _build_stages(self):
        self._stages = [
            DocumentLoadStage(),        # 阶段1: 文档加载
            TagClassificationStage(),   # 阶段2: 标签分类
            TextSplittingStage(),       # 阶段3: 文本分割
            VectorizationStage(),       # 阶段4: 向量化
            PerDocClusteringStage(),    # 阶段5: 单文档聚类摘要（并行）
            CrossDocClusteringStage(),  # 阶段6: 跨文档聚类摘要
            TagPropagationStage(),      # 阶段7: 标签传播
            IndexBuildStage(),          # 阶段8: FAISS索引构建
            PersistenceStage(),         # 阶段9: MySQL持久化
        ]
```

### 上下文对象

```python
class ForestBuildContext:
    """统一管理构建过程中的所有状态和数据"""
    docs: List[Dict]           # 原始文档（DocumentParser输出）
    source_list: List[str]     # 文档来源列表（去重排序）
    source_tag_map: Dict[str, str]  # 来源→标签映射
    chunks: List[Dict]         # L0文本块
    l1_summaries: List[Dict]   # L1单文档摘要
    l2_summaries: List[Dict]   # L2/L3跨文档摘要
    all_nodes: List[Dict]      # 所有节点（L0+L1+L2）
    hierarchy: Dict[str, List[Dict]]  # {"L0": [...], "L1": [...], "L2": [...]}
```

## 工作流程

### 构建流程

入口：`KnowledgeForest.build_forest()` 内部调用 `ForestBuildPipeline(db, knowledge_base_path).execute()`

**阶段1: 文档加载**（`pipeline.py:77`）
```python
class DocumentLoadStage:
    async def execute(self, context):
        parser = DocumentParser()
        for fname in sorted(os.listdir(context.knowledge_base_path)):
            fpath = os.path.join(context.knowledge_base_path, fname)
            if os.path.isfile(fpath):
                parsed = parser.parse_document(fpath)
                docs.extend(parsed)
        context.docs = docs
        context.source_list = sorted(set(d.get("source", "") for d in docs))
```

**阶段2: 标签分类**（`pipeline.py:125`）
```python
class TagClassificationStage:
    async def execute(self, context):
        for src in context.source_list:
            context.source_tag_map[src] = classify_document_tag(src, "")
```

**阶段3: 文本分割**（`pipeline.py:154`）
```python
class TextSplittingStage:
    async def execute(self, context):
        splitter = TextSplitter()
        chunks = splitter.split_documents(context.docs)
        for chunk in chunks:
            chunk["tag"] = context.source_tag_map.get(chunk.get("source", ""), "domain_knowledge")
        context.chunks = chunks
```

**阶段4: 向量化**（`pipeline.py:183`）
```python
class VectorizationStage:
    async def execute(self, context):
        context.embedding = TextEmbedding(model_path="./models")
        texts = [c["content"] for c in context.chunks]
        context.embedding.fit(texts)  # TF-IDF(max_features=5000, ngram_range=(1,2)) + SVD(n_components=256)
        chunk_embs = context.embedding.batch_encode(texts)
        for chunk, emb in zip(context.chunks, chunk_embs):
            chunk["embedding"] = emb
```

**阶段5: 单文档聚类摘要（并行）**（`pipeline.py:212`）
```python
class PerDocClusteringStage:
    async def execute(self, context):
        for chunk in context.chunks:
            chunk["level"] = 0
        context.hierarchy["L0"] = context.chunks
        
        doc_chunks_map = self._group_chunks_by_source(context.chunks)
        summary_gen = SummaryGenerator()
        tasks = [
            self._process_single_doc(summary_gen, source_name, doc_chunks_map[source_name])
            for source_name in sorted(doc_chunks_map.keys())
        ]
        per_doc_results = await asyncio.gather(*tasks)  # 并行执行
        
        all_per_doc_summaries = []
        for i, summaries in enumerate(per_doc_results):
            source_name = source_names[i]
            for s in summaries:
                s["source"] = source_name
                s["tag"] = context.source_tag_map.get(source_name, "domain_knowledge")
            all_per_doc_summaries.extend(summaries)
        context.l1_summaries = all_per_doc_summaries
    
    async def _process_single_doc(self, summary_gen, source_name, doc_chunks):
        import asyncio
        loop = asyncio.get_event_loop()
        doc_embs = np.array([c["embedding"] for c in doc_chunks], dtype=np.float64)
        return await loop.run_in_executor(
            None, summary_gen.per_document_summarize, doc_chunks, doc_embs, source_name
        )
```

**阶段6: 跨文档聚类摘要**（`pipeline.py:298`）
```python
class CrossDocClusteringStage:
    async def execute(self, context):
        if len(context.l1_summaries) <= 3:
            context.l2_summaries = []
        else:
            summary_gen = SummaryGenerator()
            per_doc_texts = [s["content"] for s in context.l1_summaries]
            per_doc_embs = context.embedding.batch_encode(per_doc_texts)
            
            context.l1_summaries = context.l1_summaries
            l2_summaries = summary_gen.generate_cross_document_summary(
                context.l1_summaries, per_doc_embs, level=2
            )
            
            if len(l2_summaries) > 5:
                l2_texts = [s["content"] for s in l2_summaries]
                l2_embs = context.embedding.batch_encode(l2_texts)
                l3_summaries = summary_gen.generate_cross_document_summary(
                    l2_summaries, l2_embs, level=3
                )
                l2_summaries.extend(l3_summaries)
            
            context.l2_summaries = l2_summaries
```

**阶段7: 标签传播**（`pipeline.py:350`）
```python
class TagPropagationStage:
    async def execute(self, context):
        for s in context.l2_summaries:
            cluster_sources = s.get("metadata", {}).get("sources", [])
            tag_ratios = compute_cluster_tag_ratios(
                [{"source": src} for src in cluster_sources], context.source_tag_map
            )
            s["tag_ratios"] = tag_ratios
            dominant = max(tag_ratios, key=tag_ratios.get) if tag_ratios else ""
            s["dominant_tag"] = dominant.replace("_ratio", "") if dominant else "mixed"
        
        for s in context.l1_summaries:
            s["embedding"] = context.embedding.encode(s["content"])
        for s in context.l2_summaries:
            s["embedding"] = context.embedding.encode(s["content"])
        
        context.hierarchy["L1"] = context.l1_summaries
        context.hierarchy["L2"] = context.l2_summaries
```

**阶段8: FAISS索引构建**（`pipeline.py:398`）
```python
class IndexBuildStage:
    async def execute(self, context):
        all_nodes = context.chunks + context.l1_summaries + context.l2_summaries
        context.all_nodes = all_nodes
        
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
```

**阶段9: MySQL持久化**（`pipeline.py:445`）
```python
class PersistenceStage:
    async def execute(self, context):
        context.db.query(KnowledgeNode).delete()
        for doc in context.all_nodes:
            meta = {"source": doc.get("source"), "page": doc.get("page", 0), 
                    "cluster_id": doc.get("cluster_id", 0), "level": doc.get("level", 0),
                    "tag": doc.get("tag", "domain_knowledge"), ...}
            node = KnowledgeNode(level=doc.get("level", 0), content=doc.get("content", "")[:5000],
                                 metadata_info=json.dumps(meta), parent_id=doc.get("parent_id"),
                                 cluster_id=doc.get("cluster_id", 0), tag=tag)
            context.db.add(node)
        context.db.commit()
        
        # 更新版本号
        config = context.db.query(SystemConfig).filter(SystemConfig.key == "knowledge_forest_version").first()
        version = str(int(datetime.now().timestamp()))
        config.value = version
        context.db.commit()
```

### 检索流程

**基础检索** `query(query_text, k)`（`main.py`）：
```python
async def query(self, query_text: str, k: int = 5):
    query_emb = self.embedding.encode(query_text)
    D, I = self.retrieval.index.search(np.array([query_emb], dtype=np.float32), k)
    results = []
    for i, idx in enumerate(I[0]):
        if idx < len(self.retrieval.documents):
            doc = self.retrieval.documents[idx]
            results.append({
                "source": doc.get("source", ""),
                "content": doc.get("content", "")[:500],
                "similarity": 1.0 / (1.0 + D[0][i]),
                "tag": doc.get("tag", ""),
            })
    return results
```

**标签过滤检索** `tagged_query(query_text, tag, k)`：
```python
async def tagged_query(self, query_text: str, tag: str, k: int = 5):
    results = await self.query(query_text, k * 3)  # 扩大检索范围
    return [r for r in results if r.get("tag") == tag][:k]
```

**多通道检索** `multi_channel_query(query_text, profile, k_per_channel=5)`：
```python
async def multi_channel_query(self, query_text, profile, k_per_channel=5):
    crop = profile.get("crop_type", "")
    location = profile.get("location", "")
    
    # 三个通道并行检索
    template_results = await self.tagged_query(f"{crop} {location} 灌溉规划 方案框架", "template", k_per_channel)
    tech_results = await self.tagged_query(query_text, "domain_knowledge", k_per_channel)
    standard_results = await self.tagged_query(f"{crop} 灌溉 标准规范 技术指标", "standard", k_per_channel)
    
    return {"template": template_results, "domain_knowledge": tech_results, "standard": standard_results}
```

## 决策标准

| 场景 | 决策 |
|-----|------|
| 知识库目录为空 | 跳过构建，`context.build_completed = False` |
| 文档解析失败 | 跳过该文档，继续处理后续文档 |
| L1摘要 ≤ 3 | 跳过跨文档聚类（阶段6），`context.l2_summaries = []` |
| L2 > 5 | 递归生成L3摘要 |
| 检索结果为空 | 返回空列表 `[]`，上层使用默认知识 |

## 工具使用

| 工具 | 调用方式 | 输入 | 输出 |
|-----|---------|------|------|
| `DocumentParser.parse_document()` | 本地 | PDF/TXT文件路径 | 结构化文档列表 |
| `TextSplitter.split_documents()` | 本地 | 文档列表 | 文本块列表 |
| `TextEmbedding.fit() + batch_encode()` | 本地/sklearn | 文本列表 | TF-IDF+SVD向量矩阵(dim=256) |
| `SummaryGenerator.per_document_summarize()` | 本地/sklearn(GMM) | 文本块+向量 | L1摘要列表 |
| `SummaryGenerator.generate_cross_document_summary()` | 本地/sklearn(GMM) | 摘要列表+向量 | L2/L3摘要列表 |
| `faiss.IndexIDMap(IndexFlatL2)` | 本地/faiss | 向量数组 | 向量索引(保存到faiss_index.bin) |
| `KnowledgeNode` (MySQL) | SQLAlchemy | 节点数据 | 持久化记录 |
| `SystemConfig` (MySQL) | SQLAlchemy | key="knowledge_forest_version" | 版本号更新 |

## 质量检查

构建完成后确认：
- [ ] L0 文本块数量 > 0（`len(context.chunks) > 0`）
- [ ] L1 摘要数量 > 0（有文档必有摘要）
- [ ] FAISS索引维度 = 256（`index_dim = emb_array.shape[1]`）
- [ ] 索引文件已保存：`./faiss_index.bin` + `./faiss_docs.pkl`
- [ ] MySQL KnowledgeNode 记录已更新（旧记录删除+新记录写入）
- [ ] SystemConfig 版本号已更新为当前时间戳

> **代码位置**: `backend/knowledge_forest/main.py`（KnowledgeForest类）, `backend/knowledge_forest/pipeline.py`（9阶段管道）
> **核心类**: `KnowledgeForest`, `ForestBuildPipeline`, `ForestBuildContext`
> **管道阶段**: `DocumentLoadStage`, `TagClassificationStage`, `TextSplittingStage`, `VectorizationStage`, `PerDocClusteringStage`, `CrossDocClusteringStage`, `TagPropagationStage`, `IndexBuildStage`, `PersistenceStage`