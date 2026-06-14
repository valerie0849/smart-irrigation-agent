---
name: scheduler
description: >
  调度与意图路由技能。当用户发起灌溉相关的任意请求时首先激活，
  负责判断用户意图（generate/modify/weather/soil/knowledge/knowledge_browse）
  并调度后续子技能。自动检测参数微调意图，将生成请求智能转为修改请求。
  代码位于 backend/main.py，核心方法：_classify_intent(), _analyze_missing_info()。
---

# 调度技能 — 意图路由与流程编排

## 何时使用

此技能是系统的入口调度器，位于 `backend/main.py` 的 FastAPI 路由处理中。每个用户请求到达 `/chat` 端点时首先执行意图分类。

## 工作流程

### 第1步：意图分类

调用 `_classify_intent(text)` 方法（`backend/main.py:81`），按以下优先级判定意图：

```python
def _classify_intent(text: str) -> str:
    u = text.lower()
    
    # 优先级1: modify（最优先）
    if any(kw in u for kw in MODIFY_TRIGGER_WORDS):
        return "modify"  # "修改方案", "调整方案", "优化方案", "帮我把", "改一下"等16个词
    
    # 优先级2: weather
    if any(kw in u for kw in WEATHER_TRIGGER_WORDS):
        return "weather"  # "天气", "气象", "气温", "降水", "降雨", "湿度"
    
    # 优先级3: soil
    if any(kw in u for kw in SOIL_TRIGGER_WORDS):
        return "soil"  # "土壤"
    
    # 优先级4: knowledge_browse
    if any(kw in u for kw in KNOWLEDGE_BROWSE_WORDS):
        return "knowledge_browse"  # "查看知识森林", "知识列表", "浏览知识"等13个词
    
    # 优先级5: knowledge
    if any(kw in u for kw in KNOWLEDGE_TRIGGER_WORDS):
        return "knowledge"  # "知识", "依据", "标准", "规范", "参考标准"
    
    # 优先级6: generate（显式触发词）
    if any(kw in u for kw in GEN_TRIGGER_WORDS):
        return "generate"  # "生成方案", "设计方案", "制定方案", "帮我设计"等8个词
    
    # 优先级7: generate（隐式：地点+作物）
    has_location = any(loc in text for loc in CHINA_COORDS.keys() if len(loc) > 0)
    has_crop = any(kw in text for kw in CROP_NAMES)  # CROP_NAMES = ["小麦","水稻","玉米","棉花","蔬菜","果树","大豆","花生","油菜"]
    has_area = any(kw in text for kw in ["亩", "公顷"])
    has_budget = any(kw in text for kw in ["预算", "万"])
    
    if has_location and has_crop: return "generate"
    if has_location and (has_area or has_budget): return "generate"
    if has_area and has_crop: return "generate"
    if has_area or has_budget: return "generate"
    
    return "unknown"
```

### 第2步：实体提取与累积

调用 `RequirementAgent._raw_extract_entities(ui)`（`backend/main.py:181`），提取实体后累积到 `session_entities[sid]` 字典：

```python
# 每轮对话提取新实体
new_ents = RequirementAgent._raw_extract_entities(ui)

# 合并到session级累积字典
for k, v in new_ents.items():
    if v:
        session_entities[sid][k] = v

accumulated = session_entities[sid]  # 所有历史轮次的实体
```

### 第3步：参数微调检测

当意图为 `generate` 时，检测是否应转为 `modify`（`backend/main.py:188`）：

```python
if intent == "generate":
    # 条件A: 对话历史中存在长度>500字符的assistant回复
    has_plan = any(
        m.get("role") == "assistant" and len(m.get("content", "")) > 500
        for m in ctx_history
    )
    
    # 条件B: 本轮未提取到 location/crop_type/area
    is_param_tweak = (
        not new_ents.get("location") and
        not new_ents.get("crop_type") and
        not new_ents.get("area")
    ) and (
        # 条件C: 提取到budget 或 包含修改类关键词
        new_ents.get("budget") or
        any(kw in ui for kw in ["预算", "万", "修改", "调整", "改成", "改为", 
                                "换成", "变成", "更换", "替换", "更新", "变更", 
                                "改一下", "调一下"])
    )
    
    if has_plan and is_param_tweak:
        intent = "modify"  # 转为修改意图
```

### 第4步：信息完整性检查

当意图为 `generate` 时，调用 `_analyze_missing_info()`（`backend/main.py:125`）：

```python
def _analyze_missing_info(text: str, accumulated: dict = None) -> List[str]:
    entities = RequirementAgent._raw_extract_entities(text)
    # 合并累积实体
    if accumulated:
        for k, v in accumulated.items():
            if v and not entities.get(k):
                entities[k] = v
    
    missing = []
    if not entities.get("location"):
        missing.append("📍 项目地点（如：河南中牟县、山东菏泽）")
    if not entities.get("crop_type") and not entities.get("location"):
        missing.append("🌾 作物类型（如：小麦、水稻、玉米、棉花）")
    if not entities.get("area"):
        missing.append("📐 灌溉面积（如：2000亩）")
    if not entities.get("budget"):
        missing.append("💰 预算范围（如：预算200万）")
    return missing, entities
```

### 第5步：流程路由

根据最终意图路由到不同处理逻辑（`backend/main.py:205`）：

| 意图 | 路由 | 端点 |
|-----|------|------|
| `knowledge_browse` | `_do_knowledge_browse()` | `/chat` |
| `knowledge` | `_do_knowledge()` | `/chat` |
| `weather` | `_do_weather()` | `/chat` |
| `soil` | `_do_soil()` | `/chat` |
| `generate` | 信息完整 → `/chat/generate` SSE流式；缺失 → 追问 | `/chat` + `/chat/generate` |
| `modify` | `/chat/modify` SSE流式 | `/chat` + `/chat/modify` |
| `unknown` | 有灌溉相关词 → 追问；无 → LLM回复/兜底 | `/chat` |

## 决策标准

| 场景 | 决策 |
|-----|------|
| 首次对话，无历史方案 | 正常意图分类，检测缺失信息 |
| 有历史方案(>500字符)，用户只改预算/参数 | 切换为 `modify` 意图 |
| 有历史方案，用户提供新地点/作物 | 保持 `generate` 意图（全新方案） |
| 知识森林未构建 | generate流程中自动触发 `build_forest()` |
| LLM不可用(`deepseek._loaded=false`) | 降级使用编排Agent的模板方案 |

## 工具使用

| 工具 | 调用位置 | 输入 | 输出 |
|-----|---------|------|------|
| `_classify_intent()` | `/chat` 入口 | text | "generate"/"modify"/"weather"/"soil"/"knowledge"/"knowledge_browse"/"unknown" |
| `RequirementAgent._raw_extract_entities()` | `/chat` + 各端点 | user_input | {"location", "crop_type", "area", "budget"} |
| `_analyze_missing_info()` | `/chat` generate分支 | text, accumulated | (missing_list, entities) |
| `ConversationHistory` (MySQL) | `_save_msg_to_db()` | sid, role, content | — |
| `conversation_context` (内存字典) | 全局变量 | sid → [{"role", "content"}] | 对话历史 |
| `session_entities` (内存字典) | 全局变量 | sid → {location, crop_type, area, budget} | 累积实体 |
| SSE event emitter | `/chat/generate`, `/chat/modify` | step, progress | 实时进度 |

## 质量检查

在每次路由后确认：
- [ ] 意图分类正确（7种意图分属合理）
- [ ] 参数微调检测逻辑已执行（当 intent=="generate" 且有历史方案时）
- [ ] 实体已累积到 session_entities[sid]
- [ ] 对话历史已保存到数据库（`_save_msg_to_db()`）
- [ ] 缺失信息已列出（location/crop_type/area/budget）

## 输出规范

```json
// 信息缺失时（/chat返回）
{
    "success": true,
    "session_id": "uuid",
    "reply": "好的，我来帮您生成灌溉方案！不过还需要以下信息：\n\n- 📍 项目地点...",
    "type": "clarification",
    "missing": ["项目地点", "作物类型"]
}

// generate启动时（/chat返回，触发SSE）
{
    "success": true,
    "session_id": "uuid",
    "type": "generate_start",
    "message": "河南中牟2000亩小麦，预算200万",
    "entities": {"location": "河南", "crop_type": "小麦", "area": 2000.0, "budget": 2000000}
}
```

> **代码位置**: `backend/main.py`
> **核心方法**: `_classify_intent()`(line 81), `_analyze_missing_info()`(line 125)
> **端点**: `POST /chat`, `POST /chat/generate`, `POST /chat/modify`