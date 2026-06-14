---
name: equipment-selection
description: >
  灌溉设备选型技能。根据灌溉方式和项目规模匹配设备清单，进行成本核算，
  结合知识森林提供设备配置和IoT智能控制建议，当预算不足时执行分级优化。
  代码位于 backend/agents/equipment.py，核心类：EquipmentAgent，核心方法：select()。
---

# 设备选型技能 — 设备配置与预算优化

## 何时使用

当 `IrrigationDesignAgent.design()` 返回 `irrigation_plan` 后，调用 `EquipmentAgent(db, knowledge_forest).select(irrigation_plan, budget)` 配置设备。代码位置：`backend/agents/equipment.py`。

## 工作流程

### 第1步：知识检索

调用 `_query_equipment_knowledge(method, crop_type, location)`（`equipment.py:41`）：

```python
async def _query_equipment_knowledge(self, method, crop_type, location):
    if self.knowledge_forest is None:
        return []
    
    query = f"{method} {crop_type} {location} 设备选型 配置方案"
    results = await self.knowledge_forest.query(query, k=3)
    return results
```

### 第2步：生成设备清单

调用 `_get_equipment_list(method, area, total_water)`（`equipment.py:81`），按灌溉方式选择预设模板：

**滴灌设备清单**（`equipment.py:84-93`）：
```python
base_equipment = [
    {"name": "滴灌带", "model": "DG-16-30", "quantity": int(area * 1.2), "price": 0.8, "unit": "米"},
    {"name": "滴灌管", "model": "DG-20-50", "quantity": int(area * 0.1), "price": 5.0, "unit": "米"},
    {"name": "电磁阀", "model": "SV-24V", "quantity": int(area / 50) + 1, "price": 120.0, "unit": "个"},
    {"name": "过滤器", "model": "F-200", "quantity": 2, "price": 800.0, "unit": "台"},
    {"name": "水肥一体机", "model": "HF-500", "quantity": 1, "price": 15000.0, "unit": "台"},
    {"name": "传感器", "model": "SM-100", "quantity": int(area / 100) + 1, "price": 350.0, "unit": "个"},
    {"name": "控制器", "model": "CT-200", "quantity": 1, "price": 3000.0, "unit": "台"}
]
```

**喷灌设备清单**（`equipment.py:94-103`）：
```python
base_equipment = [
    {"name": "喷头", "model": "SP-360", "quantity": int(area / 0.15), "price": 85.0, "unit": "个"},
    {"name": "喷灌管", "model": "PG-50", "quantity": int(area * 0.15), "price": 8.0, "unit": "米"},
    {"name": "电磁阀", "model": "SV-24V", "quantity": int(area / 100) + 1, "price": 120.0, "unit": "个"},
    {"name": "过滤器", "model": "F-300", "quantity": 2, "price": 1200.0, "unit": "台"},
    {"name": "水泵", "model": "P-5.5KW", "quantity": 2, "price": 2500.0, "unit": "台"},
    {"name": "传感器", "model": "SM-100", "quantity": int(area / 100) + 1, "price": 350.0, "unit": "个"},
    {"name": "控制器", "model": "CT-300", "quantity": 1, "price": 4500.0, "unit": "台"}
]
```

**沟灌设备清单**（`equipment.py:104-110`）：
```python
base_equipment = [
    {"name": "渠道衬砌", "model": "CL-10", "quantity": int(area * 0.05), "price": 120.0, "unit": "米"},
    {"name": "闸门", "model": "G-100", "quantity": 5, "price": 800.0, "unit": "个"},
    {"name": "流量计", "model": "FM-50", "quantity": 3, "price": 1500.0, "unit": "台"},
    {"name": "水泵", "model": "P-11KW", "quantity": 2, "price": 5000.0, "unit": "台"}
]
```

### 第3步：知识增强

调用 `_enrich_from_knowledge(equipment_list, knowledge)`（`equipment.py:56`）：

```python
def _enrich_from_knowledge(self, equipment_list, knowledge):
    if not knowledge:
        return equipment_list
    
    kf_text = " ".join(k.get("content", "") for k in knowledge)
    
    # 标记知识引用设备
    for item in equipment_list:
        if item["name"] in kf_text:
            item["knowledge_referenced"] = True
    
    # 检测IoT/自动化关键词
    has_iot = any(kw in kf_text for kw in ["物联网", "IoT", "传感器网络", "智能监测"])
    has_automation = any(kw in kf_text for kw in ["自动控制", "智能控制", "远程", "Docker", "Kubernetes"])
    
    if has_iot or has_automation:
        already_has_controller = any(e["name"] == "控制器" for e in equipment_list)
        if already_has_controller:
            for item in equipment_list:
                if item["name"] == "控制器":
                    item["note"] = "知识森林建议：配置智能控制与远程管理模块"
    
    return equipment_list
```

### 第4步：预算优化

调用 `_optimize_for_budget(equipment_list, budget)`（`equipment.py:114`）：

```python
def _optimize_for_budget(self, equipment_list, budget):
    total_cost = sum(item["price"] * item["quantity"] for item in equipment_list)
    ratio = budget / total_cost
    
    # 第1级优化：预算严重不足（ratio < 0.5），移除非关键设备
    if ratio < 0.5:
        non_critical_items = ["传感器", "水肥一体机", "控制器"]
        equipment_list = [item for item in equipment_list if item["name"] not in non_critical_items]
        total_cost = sum(item["price"] * item["quantity"] for item in equipment_list)
        ratio = budget / total_cost  # 重新计算比例
    
    # 第2级优化：按比例缩减
    optimized_list = []
    for item in equipment_list:
        if item["name"] in ["过滤器", "水泵"]:
            # 关键设备保持原数量
            optimized_list.append(item)
        else:
            # 其他设备按比例缩减，至少保留1个
            new_quantity = max(1, int(item["quantity"] * ratio))
            optimized_list.append({**item, "quantity": new_quantity})
    
    return optimized_list
```

### 第5步：构建结果

返回设备选型结果（`equipment.py:32`）：

```python
total_cost = sum(item["price"] * item["quantity"] for item in equipment_list)

if total_cost > budget:
    equipment_list = self._optimize_for_budget(equipment_list, budget)

return {
    "irrigation_method": irrigation_method,
    "equipment_list": equipment_list,
    "total_cost": round(total_cost, 2),
    "budget": budget,
    "remaining_budget": round(budget - total_cost, 2),
    "knowledge_sources": [k["source"] for k in kf_equip_knowledge],
}
```

## 决策标准

| 场景 | 决策 |
|-----|------|
| 检测到IoT关键词(`物联网/IoT/传感器网络/智能监测`) 且 有控制器 | 给控制器添加 "知识森林建议：配置智能控制与远程管理模块" 备注 |
| 检测到自动化关键词(`自动控制/智能控制/远程/Docker/Kubernetes`) 且 有控制器 | 同上 |
| ratio < 0.5（预算严重不足） | 移除传感器/水肥一体机/控制器 |
| 0.5 ≤ ratio < 1.0（预算略不足） | 关键设备(过滤器/水泵)保持，其他按比例缩减 |
| ratio ≥ 1.0（预算充足） | 不调用优化，保持原清单 |
| 知识森林不可用 | 跳过增强，使用默认配置 |
| 灌溉方式未知 | 默认使用"喷灌"配置 |

## 工具使用

| 工具 | 调用方式 | 输入 | 输出 |
|-----|---------|------|------|
| `KnowledgeForest.query()` | async调用 | query字符串, k=3 | 设备知识结果 |
| 本地计算 | — | area, method | 设备数量(滴灌带=area×1.2, 喷头=area/0.15, 电磁阀=area/50+1, 传感器=area/100+1) |

## 质量检查

在输出前确认：
- [ ] 每种设备有 name/model/quantity/price/unit 字段
- [ ] 关键设备（过滤器/水泵）数量未被缩减
- [ ] 每种设备至少保留1个（`max(1, int(quantity * ratio))`）
- [ ] IoT/自动化建议已标注（当知识包含相关关键词时）
- [ ] 知识参考设备已标记 `knowledge_referenced=True`
- [ ] total_cost ≤ budget（优化后）

> **代码位置**: `backend/agents/equipment.py`
> **核心方法**: `select()`(line 14), `_get_equipment_list()`(line 81), `_enrich_from_knowledge()`(line 56), `_optimize_for_budget()`(line 114), `_query_equipment_knowledge()`(line 41)