---
name: irrigation-design
description: >
  灌溉制度设计技能。根据项目概况和知识森林检索结果，使用Penman-Monteith公式
  计算参考作物需水量ET0，验证作物系数Kc，设计分生育期灌溉制度并选择灌溉方式。
  代码位于 backend/agents/irrigation_design.py，核心类：IrrigationDesignAgent，核心方法：design()。
---

# 灌溉设计技能 — 灌溉制度设计与需水量计算

## 何时使用

当 `RequirementAgent.analyze()` 返回 `project_profile` 后，调用 `IrrigationDesignAgent(db, knowledge_forest).design(profile)` 设计灌溉制度。代码位置：`backend/agents/irrigation_design.py`。

## 工作流程

### 第1步：知识检索

调用 `_query_design_knowledge(location, crop_type)`（`irrigation_design.py:56`）：

```python
async def _query_design_knowledge(self, location: str, crop_type: str) -> List[Dict[str, Any]]:
    if self.knowledge_forest is None:
        return []
    
    query = f"{crop_type} {location} 灌溉制度 灌水定额 Kc系数 设计方案"
    results = await self.knowledge_forest.query(query, k=5)
    return results
```

### 第2步：计算参考作物需水量 ET0

调用 `_calculate_et0(weather_data)`（`irrigation_design.py:112`），使用 **Penman-Monteith公式（FAO-56）**：

```python
def _calculate_et0(self, weather_data: Dict[str, Any]) -> float:
    temperature = weather_data.get("temperature", 25)
    humidity = weather_data.get("humidity", 60)
    wind_speed = weather_data.get("wind_speed", 2)
    sunshine_hours = weather_data.get("sunshine_hours", 8)
    
    try:
        # 饱和水汽压斜率
        delta = 4098 * (0.6108 * np.exp((17.27 * temperature) / (temperature + 237.3))) / ((temperature + 237.3) ** 2)
        
        # 干湿表常数
        gamma = 0.00163 * 101.3 / (2.45 * (temperature + 273.15))
        
        # 大气顶层辐射
        ra = (24 * 60 / np.pi) * 4.92 * (sunshine_hours / 12)
        
        # 太阳辐射
        rs = 0.5 * ra
        
        # 净辐射
        rn = 0.77 * rs
        
        # 土壤热通量（日尺度近似为0）
        g = 0
        
        # Penman-Monteith公式
        et0 = (0.408 * delta * (rn - g) + gamma * (900 / (temperature + 273)) * wind_speed * (0.6108 * np.exp((17.27 * temperature) / (temperature + 237.3)) - humidity / 100)) / (delta + gamma * (1 + 0.34 * wind_speed))
        
        return round(max(et0, 1.0), 2)
    except:
        return 3.5  # 异常时返回默认值
```

### 第3步：Kc系数验证

调用 `_adjust_kc_from_knowledge(crop_params, knowledge)`（`irrigation_design.py:69`）：

```python
def _adjust_kc_from_knowledge(self, crop_params, knowledge):
    adjusted = dict(crop_params)  # 复制原始参数
    
    kc_keywords = ["Kc", "作物系数", "kc"]
    found_kc_hints = 0
    for item in knowledge:
        content = item.get("content", "")
        for kw in kc_keywords:
            if kw in content:
                found_kc_hints += 1
                break
    
    # 仅当找到≥2条Kc相关记录时，标记 knowledge_validated
    if found_kc_hints >= 2:
        adjusted["knowledge_validated"] = True
    
    return adjusted  # 不修改Kc值，仅添加验证标记
```

**重要**：Kc系数以数据库/默认值为准，知识检索结果仅用于验证背书，**不修改数值**。

### 第4步：生成灌溉制度

调用 `_generate_irrigation_schedule(et0, adjusted_kc, soil_params)`（`irrigation_design.py:139`）：

```python
def _generate_irrigation_schedule(self, et0, crop_params, soil_params):
    schedule = []
    stages = [
        {"name": "苗期", "kc": crop_params.get("stage_1_kc", 0.3), "days": crop_params.get("stage_1_days", 30)},
        {"name": "分蘖期", "kc": crop_params.get("stage_2_kc", 0.5), "days": crop_params.get("stage_2_days", 45)},
        {"name": "拔节期", "kc": crop_params.get("stage_3_kc", 1.15), "days": crop_params.get("stage_3_days", 60)},
        {"name": "成熟期", "kc": crop_params.get("stage_4_kc", 0.4), "days": crop_params.get("stage_4_days", 30)}
    ]
    
    field_capacity = soil_params.get("field_capacity", 0.32)
    wilting_point = soil_params.get("wilting_point", 0.12)
    root_depth = crop_params.get("root_depth", 0.8)
    
    # 有效含水量(mm)
    available_water = (field_capacity - wilting_point) * root_depth * 1000
    
    for stage in stages:
        etc = et0 * stage["kc"]                        # 作物需水量(mm/d)
        water_requirement = etc * stage["days"]         # 阶段需水量(mm)
        irrigation_amount = available_water * 0.6       # 灌水定额(mm)，效率系数0.6
        irrigation_frequency = int(np.ceil(available_water * 0.6 / etc))  # 灌水周期(d)
        
        schedule.append({
            "stage": stage["name"],
            "duration_days": stage["days"],
            "kc_value": stage["kc"],
            "etc_mm_per_day": round(etc, 2),
            "water_requirement_mm": round(water_requirement, 2),
            "irrigation_amount_mm": round(irrigation_amount, 2),
            "irrigation_frequency_days": irrigation_frequency,
            "number_of_irrigations": int(np.ceil(stage["days"] / irrigation_frequency))
        })
    
    return schedule
```

### 第5步：选择灌溉方式

调用 `_select_irrigation_method(soil_params, knowledge)`（`irrigation_design.py:91`）：

```python
def _select_irrigation_method(self, soil_params, knowledge=None):
    soil_type = soil_params.get("soil_type", "壤土")
    
    # 优先从知识检索中统计
    method_from_knowledge = None
    if knowledge:
        drip_count = sum(1 for item in knowledge if "滴灌" in item.get("content", ""))
        spray_count = sum(1 for item in knowledge if "喷灌" in item.get("content", ""))
        if drip_count > spray_count and drip_count > 0:
            method_from_knowledge = "滴灌"
        elif spray_count > drip_count and spray_count > 0:
            method_from_knowledge = "喷灌"
    
    # 土壤类型默认
    if soil_type == "砂土":
        return method_from_knowledge if method_from_knowledge else "滴灌"
    elif soil_type == "粘土":
        return method_from_knowledge if method_from_knowledge else "沟灌"
    else:  # 壤土、粉壤土等
        return method_from_knowledge if method_from_knowledge else "喷灌"
```

### 第6步：构建结果

返回 `design_result`（`irrigation_design.py:35`）：

```python
total_water_requirement = sum(period["water_requirement_mm"] for period in irrigation_schedule)

design_result = {
    "project_info": {
        "location": location,
        "crop_type": crop_type,
        "area": area,
        "total_water_requirement_m3": round(total_water_requirement * area * 666.67 / 1000, 2),
        "irrigation_method": irrigation_method,
        "knowledge_informed": bool(kf_design_knowledge),  # 是否有知识支撑
    },
    "irrigation_schedule": irrigation_schedule,
    "calculation_details": {
        "et0": et0,
        "kc_values": adjusted_kc,
        "soil_parameters": soil_params,
        "knowledge_sources": [k["source"] for k in kf_design_knowledge],
    },
    "knowledge_learned": kf_design_knowledge,
}
```

## 决策标准

| 场景 | 决策 |
|-----|------|
| 知识森林有Kc相关记录≥2条 | 保留数据库Kc值，添加 `knowledge_validated=True` |
| 知识森林无Kc记录或查询失败 | 仅使用数据库/默认Kc值 |
| 气象数据缺失（异常时） | 使用默认ET0=3.5 mm/d |
| Penman-Monteith计算异常 | 返回默认3.5 |
| 砂土 + 知识推荐滴灌 | 选择滴灌 |
| 粘土 + 无知识推荐 | 选择沟灌 |
| 壤土 + 无知识推荐 | 选择喷灌 |
| 知识检索中"滴灌"频次>"喷灌"频次 | 选择滴灌 |

## 工具使用

| 工具 | 调用方式 | 输入 | 输出 |
|-----|---------|------|------|
| `KnowledgeForest.query()` | async调用 | query字符串, k=5 | 知识检索结果列表 |
| `numpy.exp()` | 本地计算 | 温度 | 指数值 |
| `numpy.ceil()` | 本地计算 | 浮点数 | 向上取整 |

## 质量检查

在输出前确认：
- [ ] ET0 ≥ 1.0 mm/d（计算结果取max(et0, 1.0)）
- [ ] 4个生育期全部覆盖（苗期/分蘖期/拔节期/成熟期）
- [ ] Kc值呈先升后降趋势（stage_3最高）
- [ ] 灌水周期 ≥ 1天
- [ ] 灌水定额 = available_water × 0.6
- [ ] 灌溉方式与土壤类型匹配（砂土→滴灌, 粘土→沟灌, 壤土→喷灌）
- [ ] 总需水量计算包含面积换算（mm × 亩 × 666.67 / 1000 = m³）

> **代码位置**: `backend/agents/irrigation_design.py`
> **核心方法**: `design()`(line 15), `_calculate_et0()`(line 112), `_adjust_kc_from_knowledge()`(line 69), `_generate_irrigation_schedule()`(line 139), `_select_irrigation_method()`(line 91)