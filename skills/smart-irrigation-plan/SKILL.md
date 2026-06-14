---
name: smart-irrigation-system
description: >
  智慧水利灌溉方案智能生成系统——多Agent协作领域技能集。当用户需要生成灌溉方案、
  设计灌溉制度、配置灌溉设备、审核方案质量、修改已有方案、查询气象/土壤数据或
  检索灌溉领域知识时激活。触发短语包含："生成方案"、"设计方案"、"河南"、"小麦"、
  "水稻"、"灌溉"、"喷灌"、"滴灌"、"修改方案"、"调整预算"、"改成"、"换成"、
  "天气"、"土壤"、"知识"、"查看知识森林"。
---

# 智慧水利灌溉方案智能生成系统

## 技能概述

这是一个多Agent协作的灌溉方案智能生成技能集，由7个Agent和3个支撑模块组成，覆盖从需求输入到方案输出的完整业务流程。技能采用调度Agent统一路由、需求分析Agent提取实体、灌溉设计Agent计算需水量、设备选型Agent配置设备、编排Agent生成报告、质检Agent审核质量、修改Agent支持局部修改。

## 触发条件

| 触发场景 | 触发关键词 |
|---------|-----------|
| 方案生成 | 生成方案、设计方案、制定方案、出方案、帮我设计、河南中牟2000亩小麦 |
| 方案修改 | 修改方案、调整预算、改成300万、改一下、换成 |
| 环境查询 | 天气、气象、气温、降水、降雨、湿度、土壤、土质 |
| 知识检索 | 知识、依据、标准、规范、参考标准 |
| 知识浏览 | 查看知识森林、浏览知识库、知识全貌 |

## 技能架构

```
交互展示层 ──→ 调度Agent(SchedulerAgent)
                  │
智能决策层 ──→ ├── 需求分析Agent(RequirementAgent)
                  ├── 灌溉设计Agent(IrrigationDesignAgent)
                  ├── 设备选型Agent(EquipmentAgent)
                  ├── 方案编排Agent(OrchestratorAgent)
                  ├── 质检Agent(QualityAgent)
                  └── 方案修改Agent(PlanModifyAgent)
                  │
数据中台层 ──→ ├── MySQL/SQLite (CropParameter/SoilParameter/KnowledgeNode)
                  └── Faiss向量库 (知识森林索引)
                  │
基础设施层 ──→ ├── Open-Meteo API (气象+土壤数据)
                  ├── DeepSeek LLM (API模式, deepseek.py)
                  └── 领域知识库 (PDF文献/标准规范)
```

## 核心工作流

### 方案生成流程

```
用户输入 → SchedulerAgent(意图分类→实体提取→缺失信息检测)
  → RequirementAgent(analyze: 实体提取→坐标解析→气象API→土壤API→DB查询)
  → IrrigationDesignAgent(design: 知识检索→ET0计算→Kc验证→灌溉制度→方式选择)
  → EquipmentAgent(select: 知识检索→设备清单→知识增强→预算优化)
  → OrchestratorAgent(orchestrate: 知识合并→Jinja2模板→方案说明)
  → QualityAgent(check: 合规性→合理性→充分性→知识基准→加权评分)
  → 输出方案
```

### 方案修改流程

```
用户修改指令 → SchedulerAgent(检测历史方案+参数微调→转为modify意图)
  → PlanModifyAgent(modify: LLM解析意图→BFS依赖展开→并行检索→骨架生成→合并→局部审核→质量迭代)
  → 输出修改后方案
```

## 技术栈

| 组件 | 技术 | 位置 |
|-----|------|------|
| LLM | DeepSeek-V4-Pro (API模式) | `backend/llm/deepseek.py` |
| 气象API | Open-Meteo (current参数) | `backend/data_acquisition/main.py` |
| 土壤API | Open-Meteo (hourly参数) | `backend/data_acquisition/main.py` |
| 向量库 | Faiss IndexIDMap(IndexFlatL2, dim=256) | `backend/knowledge_forest/retrieval.py` |
| 向量化 | TF-IDF(max_features=5000) + SVD(n_components=256) | `backend/knowledge_forest/embedding.py` |
| 聚类 | GaussianMixture (sklearn) | `backend/knowledge_forest/summary.py` |
| 关系库 | SQLite/MySQL | `backend/src/models.py` |
| 模板引擎 | Jinja2 (Environment + BaseLoader) | `backend/agents/orchestrator.py` |
| 知识库路径 | `C:\Users\21383\Desktop\26spring\sx\领域知识和标准文献` | `backend/knowledge_forest/main.py` |

## 质量保障

所有输出方案必须通过四维质量审核：

| 维度 | 权重 | 核心检查项 | 文件位置 |
|-----|------|-----------|---------|
| 合规性 | 35% | 灌溉水利用系数≥0.6、灌水定额≤80m³/亩、标准引用 | `backend/agents/quality.py:_check_compliance()` |
| 合理性 | 30% | Penman-Monteith(20分)、FAO(15分)、Kc(20分)、生育期(15分)、定额(15分)、频次(15分) | `backend/agents/quality.py:_check_rationality()` |
| 充分性 | 20% | 项目概况/灌溉制度/设备/说明四章节 | `backend/agents/quality.py:_check_sufficiency()` |
| 知识基准 | 15% | standard tag检索(15分)、template tag检索(10分)、利用系数(10分)、定额(5分)、标准编号(5分) | `backend/agents/quality.py:_check_knowledge_benchmark()` |

评分 ≥ 90: 优秀；75-89: 良好；60-74: 合格；< 60: 需改进

## 子技能快速索引

| 技能文件 | Agent | 核心职责 | 代码文件 |
|---------|-------|---------|---------|
| [SKILL-scheduler.md](SKILL-scheduler.md) | SchedulerAgent | 意图分类、实体提取、缺失信息检测、参数微调检测 | `backend/agents/scheduler.py` |
| [SKILL-requirement.md](SKILL-requirement.md) | RequirementAgent | 实体提取、坐标解析、Open-Meteo气象/土壤API、DB参数查询 | `backend/agents/requirement.py` |
| [SKILL-irrigation-design.md](SKILL-irrigation-design.md) | IrrigationDesignAgent | Penman-Monteith ET0计算、Kc知识验证、分生育期灌溉制度、方式选择 | `backend/agents/irrigation_design.py` |
| [SKILL-equipment.md](SKILL-equipment.md) | EquipmentAgent | 按灌溉方式匹配设备、知识增强(物联网检测)、预算优化(两级策略) | `backend/agents/equipment.py` |
| [SKILL-orchestrator.md](SKILL-orchestrator.md) | OrchestratorAgent | 三源知识合并、Jinja2五章模板渲染、方案说明生成 | `backend/agents/orchestrator.py` |
| [SKILL-quality.md](SKILL-quality.md) | QualityAgent | 四维质量审核、加权评分、改进建议生成 | `backend/agents/quality.py` |
| [SKILL-modify.md](SKILL-modify.md) | PlanModifyAgent | LLM意图解析、BFS依赖展开、分章骨架生成、字符串/正则替换合并、局部审核、质量迭代 | `backend/agents/modify.py` |
| [SKILL-knowledge-forest.md](SKILL-knowledge-forest.md) | KnowledgeForest | 9阶段管道构建(L0→L1→L2→L3)、Faiss索引、分层检索、标签过滤 | `backend/knowledge_forest/main.py`, `pipeline.py` |
| [SKILL-data-acquisition.md](SKILL-data-acquisition.md) | DataAcquisition | Open-Meteo current气象、hourly土壤、坐标解析(150+城市)、土壤类型推断 | `backend/data_acquisition/main.py` |