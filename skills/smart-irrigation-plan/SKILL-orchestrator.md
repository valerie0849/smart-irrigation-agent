---
name: scheme-orchestration
description: >
  灌溉方案编排技能。将各Agent输出（项目概况、灌溉设计、设备清单、知识参考）
  整合为结构化的Markdown灌溉方案报告，管理知识引用和溯源依据。
  代码位于 backend/agents/orchestrator.py，核心类：OrchestratorAgent，核心方法：orchestrate()。
---

# 方案编排技能 — 结构化报告生成

## 何时使用

当灌溉设计、设备选型完成，且多通道知识检索就绪后，调用 `OrchestratorAgent(db).orchestrate(data)` 生成报告。代码位置：`backend/agents/orchestrator.py`。

## 工作流程

### 第1步：知识合并

调用 `_merge_knowledge(design_knowledge, general_knowledge, equip_knowledge)`（`orchestrator.py:30`），合并三个来源的知识引用并去重：

```python
def _merge_knowledge(self, design: List, general: List, equip: List) -> List[Dict[str, Any]]:
    seen = set()
    merged = []
    for item in design + general + equip:  # 按 设计→通用→设备 顺序合并
        if isinstance(item, dict):
            key = item.get("source", "") + item.get("content", "")[:80]  # source+content前80字符去重
        elif isinstance(item, str):
            key = item
        else:
            key = str(item)
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged
```

**知识来源**：
- `design_knowledge`: `irrigation_plan.get("knowledge_learned", [])` — 灌溉设计Agent的知识检索
- `general_knowledge`: `knowledge_refs` — 调度层多通道检索（template/tech/standard三个channel）
- `equip_knowledge`: `equipment_list.get("knowledge_sources", [])` — 设备选型Agent的知识检索

### 第2步：Jinja2模板渲染

调用 `_generate_report()`（`orchestrator.py:47`），使用 Jinja2 Environment + BaseLoader 渲染 Markdown 报告：

```python
def _generate_report(self, profile, plan, equip, sources, knowledge_refs):
    weather = profile.get("weather_data", {})
    soil = profile.get("soil_parameters", {})
    proj_info = plan.get("project_info", {})
    calc = plan.get("calculation_details", {})
    schedule = plan.get("irrigation_schedule", [])
    eq_list = equip.get("equipment_list", [])
    eq_total = equip.get("total_cost", 0)
    budget = profile.get("budget", 0)
    
    tpl = Environment(loader=BaseLoader()).from_string(REPORT_TEMPLATE)
    return tpl.render(
        location=profile.get("location", ""),
        crop_type=profile.get("crop_type", ""),
        crop_inferred=profile.get("crop_inferred", False),
        area=profile.get("area", 0),
        budget=budget,
        weather=weather,
        soil=soil,
        proj=proj_info,
        calc=calc,
        schedule=schedule,
        eq_list=eq_list,
        eq_total=eq_total,
        krefs=knowledge_refs,
        sources=sources,
    )
```

### 报告模板结构

模板定义在 `REPORT_TEMPLATE` 变量（`orchestrator.py:104`），包含五章：

```markdown
# 智慧灌溉方案报告

## 一、项目概况
### 1.1 项目基本信息
- 项目地点：{{ location }}
- 作物类型：{{ crop_type }}{% if crop_inferred %} *（根据地区自动推断）*{% endif %}
- 灌溉面积：{{ area }} 亩
- 预算规模：{{ "{:,.2f}".format(budget) }} 元

### 1.2 气象条件（实时数据 - {{ weather.get('source', 'Open-Meteo') }}）
- 经纬度：({{ weather.get('latitude') }}, {{ weather.get('longitude') }})
- 平均气温：{{ weather.get('temperature') }}℃
- 相对湿度：{{ weather.get('humidity') }}%
- 参考ET0：{{ weather.get('et0') }} mm/天

### 1.3 土壤条件（实时数据 - {{ soil.get('source', 'Open-Meteo') }}）
- 土壤类型：{{ soil.get('soil_type') }}
- 田间持水量：{{ "{:.1%}".format(soil.get('field_capacity')) }}
- 萎蔫系数：{{ "{:.1%}".format(soil.get('wilting_point')) }}
- 有效含水量：{{ "{:.1%}".format(soil.get('available_water')) }}

## 二、灌溉制度设计
### 2.1 设计参数
- 计算方法：Penman-Monteith 公式（FAO-56）
- 参考作物需水量 ET0：{{ calc.get('et0') }} mm/天
- 灌溉方式：{{ proj.get('irrigation_method') }}
- 总需水量：{{ "{:,.2f}".format(proj.get('total_water_requirement_m3')) }} m³
- 灌溉水利用系数：0.85

### 2.2 生育期灌溉方案（Markdown表格，遍历 schedule 数组）
| 生育阶段 | 天数 | Kc | 日均需水(mm) | 阶段需水(mm) | 灌水定额(mm) | 灌水次数 |

## 三、设备选型清单（Markdown表格，遍历 eq_list 数组）
| 设备名称 | 型号 | 数量 | 单价(元) | 小计(元) |
- 设备总投资：{{ "{:,.2f}".format(eq_total) }} 元

## 四、溯源依据
### 4.1 知识森林参考来源（遍历 krefs 数组，显示 source/similarity/content[:120]）
### 4.2 参考标准与文献（有sources用sources，无则用4个默认标准）

## 五、方案说明
（固定文本模板）
```

### 第3步：生成方案说明

调用 `_generate_explanation(profile, plan, knowledge)`（`orchestrator.py:82`）：

```python
def _generate_explanation(self, profile, plan, knowledge=None):
    lines = []
    lines.append(f"项目位于{profile.get('location','')}，种植{profile.get('crop_type','')}，面积{profile.get('area',0)}亩。")
    
    calc = plan.get("calculation_details", {})
    lines.append(f"参考作物需水量ET0={calc.get('et0',0)}mm/天。")
    
    for p in plan.get("irrigation_schedule", []):
        lines.append(f"{p['stage']}: {p['duration_days']}天, Kc={p['kc_value']}, 需水{p['water_requirement_mm']}mm。")
    
    lines.append(f"推荐{plan.get('project_info',{}).get('irrigation_method','喷灌')}方式。")
    
    if knowledge:
        k_sources = set()
        for k in knowledge:
            if isinstance(k, dict) and k.get("source"):
                k_sources.add(k["source"])
            elif isinstance(k, str):
                k_sources.add(k)
        if k_sources:
            lines.append(f"参考知识源: {', '.join(sorted(k_sources)[:3])}")  # 最多取3个
    
    return "\n".join(lines)
```

### 第4步：构建输出

返回编排结果（`orchestrator.py:28`）：

```python
return {"content": content, "explanation": explanation, "sources": sources}
```

## 决策标准

| 场景 | 决策 |
|-----|------|
| 知识为空中无检索结果 | 模板渲染时 `krefs` 为空，显示"暂无知识森林参考来源" |
| sources 为空 | 使用4个默认标准：FAO-56、GB/T 50363-2018、GB/T 50085-2017、SL/T 699-2025 |
| crop_inferred=True | 作物类型后显示" *（根据地区自动推断）*" |
| 数据字段缺失 | 使用默认值 `""` 或 `"--"` |
| krefs 包含字符串而非字典 | 模板中 `if ref is mapping` 分支处理 |

## 工具使用

| 工具 | 调用方式 | 输入 | 输出 |
|-----|---------|------|------|
| `jinja2.Environment(loader=BaseLoader())` | 模板渲染 | REPORT_TEMPLATE + 13个变量 | Markdown文本 |
| `"{:,.2f}".format()` | 数字格式化 | 金额 | 带千分位的两位小数 |
| `"{:.1%}".format()` | 百分比格式化 | 小数 | 百分比字符串 |

## 质量检查

在输出前确认：
- [ ] 报告包含全部5个章节（项目概况/灌溉制度设计/设备选型清单/溯源依据/方案说明）
- [ ] 气象数据标注了来源（weather.get('source', 'Open-Meteo')）
- [ ] 土壤数据标注了来源（soil.get('source', 'Open-Meteo')）
- [ ] 设备清单为Markdown表格格式（遍历eq_list）
- [ ] 知识参考来源已列出（krefs为空则显示"暂无知识森林参考来源"）
- [ ] 方案说明文字生成了完整摘要（地点+作物+面积+ET0+各生育期+灌溉方式+知识源）
- [ ] 预算格式化为千分位两位小数

> **代码位置**: `backend/agents/orchestrator.py`
> **核心方法**: `orchestrate()`(line 14), `_merge_knowledge()`(line 30), `_generate_report()`(line 47), `_generate_explanation()`(line 82)
> **模板变量**: `REPORT_TEMPLATE`(line 104), 13个渲染变量