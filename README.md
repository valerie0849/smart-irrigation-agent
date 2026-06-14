# 智慧水利灌溉方案智能生成系统

基于多 引擎 协作与知识森林（Ψ-RAG）的智能灌溉方案生成平台，能够根据用户输入自动生成符合国家标准的完整灌溉方案。

***

## 目录

- [一、系统概述](#一系统概述)
- [二、核心功能](#二核心功能)
- [三、系统架构](#三系统架构)
- [四、核心业务流程](#四核心业务流程)
- [五、引擎职责详解](#五引擎职责详解)
- [六、知识森林（Ψ-RAG）](#六知识森林ψ-rag)
- [七、质量审核体系](#七质量审核体系)
- [八、LLM 集成](#八llm-集成)
- [九、数据采集](#九数据采集)
- [十、API 接口文档](#十api-接口文档)
- [十一、意图识别与路由](#十一意图识别与路由)
- [十二、前端界面](#十二前端界面)
- [十三、项目目录结构](#十三项目目录结构)
- [十四、环境配置](#十四环境配置)
- [十五、安装与运行](#十五安装与运行)
- [十六、依赖项](#十六依赖项)
- [十七、版本记录](#十七版本记录)

***

## 一、系统概述

智慧水利灌溉方案智能生成系统是一个基于多引擎协作的智能灌溉方案生成平台，采用四层架构设计，融合知识森林（Ψ-RAG）、实时气象数据、土壤数据和专家规则，能够自动生成符合国家标准的专业灌溉方案。

### 技术栈

| 层级    | 技术                          |
| ----- | --------------------------- |
| 后端框架  | FastAPI + Uvicorn           |
| 前端    | React 18 (CDN) + KaTeX 公式渲染 |
| 数据库   | SQLite（默认）/ MySQL（可选）       |
| 向量数据库 | Faiss                       |
| LLM   | DeepSeek-V4-Pro（API 模式）     |
| 嵌入模型  | Text2Vec / M3E              |
| 文档解析  | PyMuPDF + pdfplumber        |
| 气象数据  | Open-Meteo API              |

***

## 二、核心功能

| 功能模块     | 说明                                                |
| -------- | ------------------------------------------------- |
| **方案生成** | 输入地点、作物、面积、预算，自动生成完整灌溉方案（含流式进度反馈）                 |
| **方案修改** | 支持对已有方案进行局部修改，含依赖分析、分章生成、一致性检查                    |
| **质量审核** | 四维质量评估体系（合规性 35% + 合理性 30% + 充分性 20% + 知识库基准 15%） |
| **知识检索** | 知识森林浏览 + 关键词检索 + 多通道检索（模板/技术/标准）                  |
| **气象查询** | 实时气象数据查询（气温、湿度、风速、ET0）                            |
| **土壤查询** | 土壤数据查询（类型、温度、田间持水量、萎蔫系数）                          |
| **方案导出** | 支持 Markdown / TXT / DOCX 格式导出                     |
| **会话管理** | 多会话支持，历史记录持久化，会话切换                                |

***

## 三、系统架构

### 四层架构设计

```
┌─────────────────────────────────────────────────────┐
│                   交互展示层                          │
│  Web前端（React 18）  │  RESTful API 网关             │
├─────────────────────────────────────────────────────┤
│                   智能决策层                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│  │ 需求分析  │ │ 灌溉设计  │ │ 设备选型  │             │
│  │  引擎   │ │  引擎   │ │  引擎   │             │
│  └──────────┘ └──────────┘ └──────────┘             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│  │ 方案编排  │ │  质检    │ │ 方案修改  │             │
│  │  引擎   │ │  引擎   │ │  引擎   │             │
│  └──────────┘ └──────────┘ └──────────┘             │
├─────────────────────────────────────────────────────┤
│                   数据中台层                          │
│  SQLite / MySQL  │  Faiss 向量库  │  API 网关        │
├─────────────────────────────────────────────────────┤
│                   基础设施层                          │
│  Open-Meteo API  │  领域知识库  │  DeepSeek LLM      │
└─────────────────────────────────────────────────────┘
```

| 层级        | 组件                     | 职责                          |
| --------- | ---------------------- | --------------------------- |
| **交互展示层** | Web 前端、API 网关          | 用户交互、请求路由、静态资源服务、SSE 流式推送   |
| **智能决策层** | 6 个引擎                | 核心业务逻辑处理、多引擎协作、方案生成与修改 |
| **数据中台层** | SQLite、Faiss、API 网关    | 数据存储、向量检索、数据清洗与聚合           |
| **基础设施层** | Open-Meteo API、知识库、LLM | 外部数据获取、知识来源、AI 内容生成         |

***

## 四、核心业务流程

### 4.1 方案生成流程

```
用户输入 → 意图识别 → 信息完整性检查 → 需求分析 → 知识森林初始化
→ 灌溉制度设计 → 设备选型 → 多通道知识检索 → 方案编排 → 质量审核
→ LLM 方案优化 → 流式输出结果
```

**详细步骤：**

1. **意图识别**：`/chat` 接口接收用户消息，通过关键词匹配 + 实体提取判断意图
2. **信息补全**：如果缺少必要信息（地点、作物、面积、预算），返回澄清问题
3. **需求分析**：`RequirementEngine` 提取实体参数，获取气象/土壤数据，查询作物参数
4. **知识森林**：如果未构建，自动初始化或从缓存加载
5. **灌溉设计**：`IrrigationDesignEngine` 计算 ET0、调整 Kc、生成灌水周期、选择灌溉方式
6. **设备选型**：`EquipmentEngine` 匹配设备清单、核算成本、预算优化
7. **多通道检索**：从知识森林中按模板/技术/标准三通道检索知识依据
8. **方案编排**：`OrchestratorEngine` 整合所有数据，生成结构化报告
9. **质量审核**：`QualityEngine` 对方案进行四维评估
10. **LLM 优化**：DeepSeek 对方案内容进行最终优化和润色
11. **流式输出**：通过 SSE 向客户端实时推送进度和最终结果

### 4.2 方案修改流程

```
用户输入 → 意图识别 → 解析修改意图 → BFS 依赖展开 → 并行检索知识包
→ 骨架-填充式分章生成 → 合并方案 → 一致性检查 → 质量审核
→（不达标则重试）→ 生成 diff 对比 → 流式输出
```

**修改流程特点：**

- **依赖分析**：通过 BFS 展开受影响章节的上下游依赖
- **分章生成**：只修改受影响章节，保持其余章节不变
- **质量兜底**：评分 < 75 分自动重新生成
- **Diff 对比**：前端展示修改前后的差异对比

***

## 五、引擎职责详解

### 5.1 RequirementEngine（需求分析引擎）

**文件**：`backend/agents/requirement.py`

**职责**：

- 从用户输入中提取实体信息（地点、作物、面积、预算）
- 根据地区自动推断最适合作物（31 省份 → 作物映射表）
- 调用 DataAcquisition 获取实时气象和土壤数据
- 从数据库查询作物参数（Kc 系数、根系深度、生长阶段天数）
- 返回结构化的项目概况（project\_profile）

**关键方法**：

- `analyze(user_input, accumulated)` — 主分析入口
- `_raw_extract_entities(text)` — 实体提取（正则 + 关键词）
- `_infer_crop_by_location(location)` — 根据地区推断作物

### 5.2 IrrigationDesignEngine（灌溉设计引擎）

**文件**：`backend/agents/irrigation_design.py`

**职责**：

- 计算参考蒸散量 ET0
- 从知识森林检索设计知识，调整作物系数 Kc
- 生成分阶段灌溉制度（灌水次数、灌水定额、灌水周期）
- 计算总需水量
- 选择灌溉方式（滴灌/喷灌/沟灌）

**关键方法**：

- `design(project_profile)` — 主设计入口
- `_calculate_et0(weather_data)` — ET0 计算
- `_adjust_kc_from_knowledge(crop_params, knowledge)` — Kc 调整
- `_generate_irrigation_schedule(et0, kc, soil)` — 灌水排程
- `_select_irrigation_method(soil, knowledge)` — 灌溉方式选择

### 5.3 EquipmentEngine（设备选型引擎）

**文件**：`backend/agents/equipment.py`

**职责**：

- 根据灌溉方式和面积匹配设备清单
- 从知识森林检索设备配置知识
- 计算设备总成本
- 按预算约束进行优化

**关键方法**：

- `select(irrigation_plan, budget)` — 主选型入口
- `_get_equipment_list(method, area, water)` — 设备清单生成
- `_enrich_from_knowledge(equipment, knowledge)` — 知识增强
- `_optimize_for_budget(equipment, budget)` — 预算优化

### 5.4 OrchestratorEngine（方案编排引擎）

**文件**：`backend/agents/orchestrator.py`

**职责**：

- 整合所有引擎输出数据
- 使用 Jinja2 模板生成结构化报告
- 生成技术说明和解释
- 去重合并知识引用

**关键方法**：

- `orchestrate(data)` — 主编排入口
- `_merge_knowledge(...)` — 知识去重合并
- `_generate_report(...)` — 模板渲染
- `_generate_explanation(...)` — 方案说明

### 5.5 QualityEngine（质检引擎）

**文件**：`backend/agents/quality.py`

**职责**：

- 四维质量评估（合规性 35% + 合理性 30% + 充分性 20% + 知识库基准 15%）
- 合规性检查：标准引用、灌溉水利用系数、灌水定额
- 合理性检查：计算逻辑、参数合理性
- 充分性检查：章节完整性、内容覆盖度
- 知识库基准：与知识森林中的标准规范交叉验证

**关键方法**：

- `check(plan_content, profile)` — 主审核入口
- `_check_compliance(...)` — 合规性检查
- `_check_rationality(...)` — 合理性检查
- `_check_sufficiency(...)` — 充分性检查
- `_check_knowledge_benchmark(...)` — 知识库基准

### 5.6 PlanModifyEngine（方案修改引擎）

**文件**：`backend/agents/modify.py`

**职责**：

- 8 步修改流程：意图解析 → 依赖展开 → 知识检索 → 骨架生成 → 合并 → 审核 → 重试 → Diff
- 仅修改受影响章节，保持其余部分不变
- 自动质量兜底（评分 < 75 分重新生成）
- 生成 diff 对比数据供前端展示

**关键方法**：

- `modify(original_plan, user_request)` — 主修改入口
- `_step1_parse_intent(...)` — LLM 解析修改意图
- `_step2_bfs_expand(...)` — BFS 依赖展开
- `_step3_retrieve(...)` — 并行知识检索
- `_step4_skeleton_generate(...)` — 骨架-填充式生成
- `_step5_merge_and_check(...)` — 合并 + 交叉引用
- `_step6_audit(...)` — 局部审核 + 一致性
- `_generate_diff(...)` — 生成 diff 数据

**方案章节结构**：

| 章节       | 标识            | 内容            |
| -------- | ------------- | ------------- |
| 一、项目概况   | `overview`    | 项目背景、现状与形势    |
| 二、灌溉制度设计 | `irrigation`  | 灌水定额、灌水周期、需水量 |
| 三、设备选型   | `equipment`   | 设备清单、成本核算     |
| 四、溯源依据   | `references`  | 标准规范引用、知识来源   |
| 五、方案说明   | `explanation` | 技术说明、注意事项     |

**章节依赖关系**：

```
overview → irrigation → equipment → references
                ↓              ↓
           explanation ←────────┘
```

***

## 六、知识森林（Ψ-RAG）

### 6.1 三层结构

```
┌─────────────────────────────────────────┐
│  L2: 跨文档摘要（跨文档聚类生成）        │
│  ├─ L2.1: 跨文档聚类摘要                 │
│  └─ L3: 递归高层摘要（>5个L2时触发）     │
├─────────────────────────────────────────┤
│  L1: 单文档摘要（文档内聚类生成）         │
│  └─ 每个文档内的聚类摘要                 │
├─────────────────────────────────────────┤
│  L0: 文本块（原始文档切分后的片段）       │
│  └─ 每个文档的文本片段                   │
└─────────────────────────────────────────┘
```

### 6.2 构建管道（9 个阶段）

| 阶段 | 类名                        | 职责                            |
| -- | ------------------------- | ----------------------------- |
| 1  | `DocumentLoadStage`       | 从知识库路径加载并解析所有文档（PDF/DOCX/TXT） |
| 2  | `TagClassificationStage`  | 自动识别文档标签（模板/标准/领域知识）          |
| 3  | `TextSplittingStage`      | 将文档按语义边界切分为文本块                |
| 4  | `VectorizationStage`      | 将文本块转换为嵌入向量（Text2Vec/M3E）     |
| 5  | `PerDocClusteringStage`   | 单文档内聚类 + 摘要生成（并行处理）           |
| 6  | `CrossDocClusteringStage` | 跨文档聚类 + 高层摘要生成                |
| 7  | `TagPropagationStage`     | 标签比例计算 + L1/L2 嵌入生成           |
| 8  | `IndexBuildStage`         | 构建 Faiss 向量索引并保存到磁盘           |
| 9  | `PersistenceStage`        | 持久化到数据库（SQLite/MySQL）         |

### 6.3 文档标签体系

| 标签                 | 含义   | 文档类型示例                 |
| ------------------ | ---- | ---------------------- |
| `template`         | 结构模板 | 政府灌溉规划文件，用于提取方案框架和章节结构 |
| `standard`         | 合规标尺 | 国标/行标/技术规范，用于合规性校验     |
| `domain_knowledge` | 领域知识 | 学术论文、技术文献，用于技术参数填充     |

### 6.4 多通道检索

检索时自动分为三个通道，确保不同维度的知识覆盖：

| 通道   | 标签                 | 查询增强             | 用途     |
| ---- | ------------------ | ---------------- | ------ |
| 模板通道 | `template`         | 灌溉发展规划 方案框架 章节结构 | 方案结构框架 |
| 技术通道 | `domain_knowledge` | 灌溉技术 需水量 灌水定额 渠系 | 技术参数填充 |
| 标准通道 | `standard`         | 灌溉标准 规范要求 技术指标   | 合规性校验  |

### 6.5 增量更新

- 启动时自动检测 `领域知识和标准文献/` 目录中的新文档
- 根据增量比例决定执行增量更新还是全量重建
- 增量更新在后台线程执行，不阻塞主服务
- 支持热更新：更新完成后自动切换为新索引

### 6.6 持久化

- **Faiss 索引**：保存到 `faiss_index.bin`
- **文档元数据**：保存到 `faiss_docs.pkl`（含层级结构、标签映射）
- **数据库**：节点信息保存到 `knowledge_nodes` 表（SQLite/MySQL）

***

## 七、质量审核体系

### 7.1 四维评分模型

| 维度                | 权重  | 满分  | 检查内容                          |
| ----------------- | --- | --- | ----------------------------- |
| 合规性 (compliance)  | 35% | 100 | 灌溉水利用系数、灌水定额、标准引用格式           |
| 合理性 (rationality) | 30% | 100 | Penman-Monteith 公式、Kc 系数、灌水次数 |
| 充分性 (sufficiency) | 20% | 100 | 方案完整性、章节覆盖度、数据充分性             |
| 知识库基准 (benchmark) | 15% | 100 | 与知识森林中标准规范条款的交叉验证             |

### 7.2 评分等级

| 总分    | 等级  | 说明            |
| ----- | --- | ------------- |
| ≥ 90  | 优秀  | 各方面表现优异，可直接使用 |
| 75-89 | 良好  | 质量较好，有少量优化空间  |
| 60-74 | 合格  | 基本达标，建议补充完善   |
| < 60  | 需改进 | 存在明显问题，建议重新生成 |

### 7.3 方案修改中的质量审核

- **局部审核**：仅对修改章节进行合规性 + 合理性评估
- **全文审核**：使用 Quality引擎 对完整方案进行四维评估
- **一致性检查**：预算一致性、设备与灌溉方式一致性、章节长度变化
- **自动重试**：评分 < 75 分时，自动将问题反馈给 LLM 重新生成

***

## 八、LLM 集成

### 8.1 模型配置

| 配置项      | 默认值                           | 说明        |
| -------- | ----------------------------- | --------- |
| 模型       | DeepSeek-V4-Pro               | 通过 API 调用 |
| API 地址   | `https://api.deepseek.com/v1` | 可配置       |
| 温度       | 0.3                           | 保证输出稳定性   |
| Top-P    | 0.9                           | 采样参数      |
| 最大 Token | 4096                          | 方案生成      |

### 8.2 Prompt 设计

**方案生成 Prompt**（三步流程）：

1. **搭框架**：从模板通道检索政府规划文件，提取章节结构
2. **填内容**：从技术通道检索学术论文，填充技术参数和方法
3. **做校验**：从标准通道检索国标/行标，进行合规性比对

**方案修改 Prompt**：

- 关键约束参数（如预算）设为最高优先级
- 提供上/下文章节以保持衔接
- 要求先输出骨架再填充内容
- 标注知识来源 `【来源：文档名】`

### 8.3 降级策略

- API 不可用时：使用规则引擎生成基础方案（`Orchestrator引擎` 的模板渲染）
- 用户可感知 LLM 状态（前端显示"云端API"/"未连接"）

***

## 九、数据采集

### 9.1 气象数据

**数据源**：Open-Meteo API（实时）

| 指标    | 字段                           | 单位   |
| ----- | ---------------------------- | ---- |
| 气温    | `temperature_2m`             | °C   |
| 相对湿度  | `relative_humidity_2m`       | %    |
| 风速    | `wind_speed_10m`             | m/s  |
| 参考蒸散量 | `et0_fao_evapotranspiration` | mm/天 |

### 9.2 土壤数据

**数据源**：Open-Meteo API

| 指标   | 字段                       | 说明                   |
| ---- | ------------------------ | -------------------- |
| 土壤温度 | `soil_temperature_0cm`   | 表层温度                 |
| 土壤湿度 | `soil_moisture_0_to_1cm` | 表层至深层湿度              |
| 土壤类型 | 推断                       | 根据湿度推断（粘土/壤土/砂土/粉壤土） |

### 9.3 坐标覆盖

支持全国 31 个省份、100+ 个主要城市及区县坐标，通过 `CHINA_COORDS` 字典进行模糊匹配。

***

## 十、API 接口文档

### 10.1 对话接口

#### `POST /chat`

对话入口，进行意图识别和路由。

**请求体**：

```json
{
  "session_id": "uuid-string (可选)",
  "message": "河南中牟县2000亩小麦，预算200万"
}
```

**响应**：

```json
{
  "success": true,
  "session_id": "uuid-string",
  "reply": "...",
  "type": "generate_start | modify_start | clarification | weather | soil | knowledge | conversation"
}
```

#### `POST /chat/generate`

方案生成接口（SSE 流式）。

**SSE 事件类型**：

| 事件类型       | 说明                                                                                                |
| ---------- | ------------------------------------------------------------------------------------------------- |
| `progress` | 进度更新（step: start/requirement/knowledge/design/equipment/knowledge\_query/orchestrate/quality/llm） |
| `complete` | 生成完成，包含完整方案内容、质量报告、知识依据                                                                           |

### 10.2 方案修改接口

#### `POST /chat/modify`

方案修改接口（SSE 流式）。

**请求体**：

```json
{
  "session_id": "uuid-string",
  "message": "把预算改成300万",
  "original_plan": "原始方案内容..."
}
```

**SSE 事件**：`complete` 事件包含 diff 数据、受影响模块、质量报告。

### 10.3 知识森林接口

#### `POST /knowledge/build`

手动构建知识森林。

#### `GET /knowledge/status`

获取知识森林状态。

**响应**：

```json
{
  "is_built": true,
  "L0_chunks": 150,
  "L1_summaries": 45,
  "L2_summaries": 12,
  "total_nodes": 207,
  "clusters": 15,
  "knowledge_base_path": "C:/Users/.../领域知识和标准文献/"
}
```

#### `POST /query-knowledge`

知识检索。

**请求体**：

```json
{
  "query": "小麦灌溉制度",
  "tag": "domain_knowledge"
}
```

### 10.4 数据采集接口

#### `POST /acquire-weather`

```json
{ "location": "河南中牟县" }
```

#### `POST /acquire-soil`

```json
{ "location": "河南中牟县" }
```

### 10.5 其他接口

| 接口                           | 方法  | 说明                       |
| ---------------------------- | --- | ------------------------ |
| `/health`                    | GET | 健康检查                     |
| `/greeting`                  | GET | 欢迎语                      |
| `/llm/status`                | GET | LLM 状态                   |
| `/conversation/{session_id}` | GET | 获取会话历史                   |
| `/export/{session_id}`       | GET | 导出方案（?fmt=md\|docx\|txt） |
| `/`                          | GET | 前端页面                     |
| `/lib/{filename}`            | GET | 前端静态资源                   |

***

## 十一、意图识别与路由

### 11.1 意图分类

系统通过 `_classify_intent()` 函数对用户输入进行意图分类：

| 意图                 | 优先级 | 触发条件                      |
| ------------------ | --- | ------------------------- |
| `modify`           | 最高  | 包含"修改方案"、"调整方案"、"改成"等关键词  |
| `weather`          | 高   | 包含"天气"、"气象"、"气温"、"降水"等    |
| `soil`             | 高   | 包含"土壤"关键词                 |
| `knowledge_browse` | 中   | 包含"查看知识森林"、"浏览知识库"等       |
| `knowledge`        | 中   | 包含"知识"、"依据"、"标准"、"规范"等    |
| `generate`         | 低   | 包含"生成方案"、或同时有地点+作物、面积+预算等 |
| `unknown`          | 兜底  | 以上都不匹配，走 LLM 对话或默认回复      |

### 11.2 触发词列表

**方案生成触发词**：

```
生成方案、设计方案、制定方案、出方案、帮我设计、帮我生成、帮我做方案、
来一份方案、给我一份方案
```

**方案修改触发词**：

```
修改方案、对已有方案提出修改、调整方案、优化方案、改进方案、帮我把、
参数调整、变更方案、调整一下、改一下、换一下、修改一下
```

**知识查询触发词**：

```
知识、依据、标准、规范、规定、参考标准、技术标准、参考文献
```

***

## 十二、前端界面

### 12.1 技术栈

- **React 18**：通过 CDN 加载（`react.min.js` + `react-dom.min.js`）
- **KaTeX**：CDN 加载，渲染数学公式（`$$...$$` 和 `$...$`）
- **Marked**：CDN 加载，Markdown 渲染
- **Axios**：CDN 加载，HTTP 请求

### 12.2 界面布局

```
┌─────────────────────────────────────────────────┐
│  Header: 标题 + LLM 状态指示器                    │
├────────┬────────────────────────────────────────┤
│        │              Chat Area (50%)           │
│ Sidebar│  ┌──────────────────────────────────┐  │
│ 会话列表 │  │ 聊天消息（Markdown + KaTeX 渲染）  │  │
│        │  │ - 用户消息                         │  │
│ 新建会话 │  │ - 助手回复                         │  │
│        │  │ - 方案卡片（含编辑按钮）              │  │
│        │  │ - 气象/土壤数据卡片                 │  │
│        │  │ - 质量评估报告                      │  │
│        │  │ - 知识依据列表                      │  │
│        │  └──────────────────────────────────┘  │
│        ├────────────────────────────────────────┤
│        │        Editor Panel (50%)              │
│        │  方案编辑器（textarea + 工具栏）        │
│        │  - 保存 / 撤回 / 下载                   │
│        │  - 修改对比视图（红/绿 diff）           │
├────────┴────────────────────────────────────────┤
│  Input Area: 输入框（Enter 发送，Shift+Enter 换行）│
└─────────────────────────────────────────────────┘
```

### 12.3 核心功能

- **方案编辑**：点击方案卡片的"编辑"按钮，在右侧面板编辑
- **修改对比**：修改后自动显示 diff 视图（红色=修改前，绿色=修改后）
- **质量报告**：方案生成后自动展示四维评分和优化建议
- **知识依据**：可展开查看方案引用的知识来源
- **公式渲染**：自动将 LaTeX 公式渲染为数学表达式
- **导出下载**：支持 Markdown / TXT 格式下载

***

## 十三、项目目录结构

```
sx/
├── backend/                          # 后端代码
│   ├── agents/                       # 引擎 模块
│   │   ├── __init__.py
│   │   ├── requirement.py            # 需求分析 引擎
│   │   ├── irrigation_design.py      # 灌溉设计 引擎
│   │   ├── equipment.py              # 设备选型 引擎
│   │   ├── orchestrator.py           # 方案编排 引擎
│   │   ├── quality.py                # 质检 引擎
│   │   └── modify.py                 # 方案修改 引擎
│   ├── knowledge_forest/             # 知识森林模块
│   │   ├── __init__.py               # 导出 KnowledgeForest
│   │   ├── main.py                   # KnowledgeForest 核心类
│   │   ├── pipeline.py               # 构建管道（9 阶段）
│   │   ├── parser.py                 # 文档解析器（PDF/DOCX/TXT）
│   │   ├── splitter.py               # 文本分割器
│   │   ├── embedding.py              # 向量化器（Text2Vec/M3E）
│   │   ├── clustering.py             # 聚类器
│   │   ├── summary.py                # 摘要生成器
│   │   ├── retrieval.py              # 检索器（Faiss）
│   │   ├── tags.py                   # 标签分类器
│   │   └── incremental.py            # 增量更新器
│   ├── data_acquisition/             # 数据采集模块
│   │   ├── __init__.py
│   │   └── main.py                   # Open-Meteo API 封装 + 中国坐标字典
│   ├── llm/                          # LLM 模块
│   │   ├── __init__.py
│   │   └── deepseek.py               # DeepSeek 客户端 + Prompt 构建
│   ├── src/                          # 数据模型
│   │   └── models.py                 # SQLAlchemy 模型 + 数据库初始化
│   └── main.py                       # FastAPI 入口 + 全部 API 路由
├── frontend/                         # 前端代码
│   ├── lib/                          # 第三方库（CDN 备用）
│   │   ├── react.min.js
│   │   ├── react-dom.min.js
│   │   ├── axios.min.js
│   │   └── marked.min.js
│   └── index.html                    # 单页应用（React + KaTeX + Marked）
├── models/                           # 模型文件
│   ├── tfidf_vectorizer.pkl
│   └── svd_transformer.pkl
├── skills/                           # 技能定义
│   └── smart-irrigation-plan/
│       └── SKILL.md                  # 技能描述文档
├── 领域知识和标准文献/                # 知识库（PDF/DOCX 文献）
│   ├── 11.全球智慧灌溉技术发展态势研究_乔冬梅.pdf
│   ├── 15.新疆五家渠灌区渠系优化配水研究_李旭.pdf
│   ├── 17.和县农田灌溉发展规划.pdf
│   ├── 19.宁夏农田灌溉发展规划（征求意见稿）.pdf
│   └── 22.《广州市农田灌溉发展规划》（公众版）.docx
├── sx/                               # Python 虚拟环境（venv）
├── .env                              # 环境变量配置
├── requirements.txt                  # Python 依赖
├── run.bat                           # Windows 一键启动脚本
├── faiss_docs.pkl                    # 知识森林缓存（文档元数据）
├── faiss_index.bin                   # 知识森林缓存（Faiss 索引）
├── irrigation.db                     # SQLite 数据库（自动生成）
├── 知识森林增量更新方案.docx          # 方案文档
├── 知识森林构建流程详解.md            # 技术文档
├── 水利方案输出1md.md                 # 方案示例
├── 多引擎实现方法与底层逻辑.md        # 技术文档
└── README.md                         # 本文件
```

***

## 十四、环境配置

### 14.1 .env 文件

```bash
# ──── 数据库配置 ────
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=irrigation_db
# 不使用 MySQL 时自动使用 SQLite

# ──── DeepSeek LLM 配置 ────
DEEPSEEK_MODEL_NAME=deepseek-v4-pro
DEEPSEEK_API_KEY=sk-your-api-key
DEEPSEEK_API_BASE=https://api.deepseek.com/v1

# ──── 嵌入模型路径 ────
M3E_MODEL_PATH=./models/m3e-base
TEXT2VEC_MODEL_PATH=./models/text2vec-base-chinese

# ──── 气象数据 API ────
CMA_API_KEY=your_cma_api_key
OPENMETEO_API_URL=https://api.open-meteo.com/v1/forecast

# ──── 知识库路径 ────
FAISS_INDEX_PATH=./faiss_index/
KNOWLEDGE_BASE_PATH=C:/Users/21383/Desktop/26spring/sx/领域知识和标准文献/
```

### 14.2 数据库说明

- **默认使用 SQLite**：数据库文件自动创建在项目根目录 `irrigation.db`
- **可选 MySQL**：配置 `.env` 中的 MySQL 连接信息后自动切换
- 数据库表结构在 `backend/src/models.py` 中定义，首次启动自动创建

***

## 十五、安装与运行

### 15.1 环境要求

- Python 3.12+
- Windows 10/11（或 Linux/macOS）
- 网络连接（用于 Open-Meteo API 和 DeepSeek API）

### 15.2 安装步骤

```bash
# 1. 克隆或进入项目目录
cd sx

# 2. 激活虚拟环境
.\sx\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
# 编辑 .env 文件，填入 DEEPSEEK_API_KEY

# 5. 启动服务
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### 15.3 一键启动（Windows）

```bash
# 双击 run.bat 或命令行执行
run.bat
```

脚本会自动：

1. 检查并释放端口 8000
2. 启动后端服务
3. 等待服务就绪
4. 自动打开浏览器访问 `http://localhost:8000`

### 15.4 首次使用

1. 启动后访问 `http://localhost:8000`
2. 系统会自动检测 `领域知识和标准文献/` 目录下的文档并构建知识森林
3. 构建完成后，即可开始使用方案生成功能

### 15.5 手动构建知识森林

```bash
curl -X POST http://localhost:8000/knowledge/build
```

### 15.6 添加新文献

1. 将新的 PDF/DOCX 文件放入 `领域知识和标准文献/` 目录
2. 重启服务，系统会自动检测并执行增量更新
3. 或手动调用 `POST /knowledge/build` 触发全量重建

***

## 十六、依赖项

### 核心依赖

| 包名            | 版本      | 用途        |
| ------------- | ------- | --------- |
| fastapi       | 0.115.0 | Web 框架    |
| uvicorn       | 0.30.6  | ASGI 服务器  |
| sqlalchemy    | 2.0.34  | ORM 数据库操作 |
| pydantic      | 2.8.2   | 数据验证      |
| python-dotenv | 1.0.1   | 环境变量加载    |
| numpy         | 1.26.4  | 数值计算      |
| faiss-cpu     | 1.8.0   | 向量检索      |
| scikit-learn  | 1.5.1   | 机器学习工具    |
| torch         | 2.4.0   | 深度学习框架    |
| transformers  | 4.44.2  | 预训练模型     |

### 文档处理

| 包名         | 版本      | 用途       |
| ---------- | ------- | -------- |
| pymupdf    | 1.24.11 | PDF 解析   |
| pdfplumber | 0.11.4  | PDF 表格提取 |
| pandas     | 2.2.2   | 数据处理     |

### LLM 与 RAG

| 包名                  | 版本     | 用途          |
| ------------------- | ------ | ----------- |
| langchain           | 0.2.14 | RAG 框架      |
| langchain-community | 0.2.12 | 社区扩展        |
| aiohttp             | 3.10.5 | 异步 HTTP 客户端 |
| jinja2              | 3.1.4  | 模板引擎        |
| requests            | 2.32.3 | HTTP 请求     |

### 其他

| 包名               | 版本     | 用途        |
| ---------------- | ------ | --------- |
| umap-learn       | 0.5.6  | 降维可视化     |
| llama-cpp-python | 0.3.23 | 本地 LLM 推理 |

***

