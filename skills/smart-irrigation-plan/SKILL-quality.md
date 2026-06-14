---
name: quality-inspection
description: >
  方案质量审核技能。对灌溉方案进行四维质量评估（合规性35%、合理性30%、
  充分性20%、知识基准15%），生成评分和等级，提供改进建议。
  代码位于 backend/agents/quality.py，核心类：QualityAgent，核心方法：check()。
---

# 质检技能 — 四维质量审核

## 何时使用

当 `OrchestratorAgent.orchestrate()` 完成报告生成后，调用 `QualityAgent(db, knowledge_forest).check(plan_content, profile)` 进行审核。代码位置：`backend/agents/quality.py`。

## 工作流程

### 第1步：合规性检查（权重35%）

调用 `_check_compliance(content, profile)`（`quality.py:139`），初始100分，违规扣分：

```python
def _check_compliance(self, content: str, profile: Dict[str, Any] = None) -> Dict:
    details = []
    score = 100
    
    # 1. 灌溉水利用系数检查（正则匹配）
    water_coef_pattern = r"灌溉(水)?利用系数[\s\S]*?(\d+\.?\d*)"
    m = re.search(water_coef_pattern, content)
    if m:
        val = float(m.group(2))
        if val < 0.6:
            details.append(f"[!] 灌溉水利用系数 {val} < 0.6，不符合国标要求")
            score -= 20
        else:
            details.append(f"[✓] 灌溉水利用系数 {val} ≥ 0.6，符合国标 (GB/T 50363-2018)")
    else:
        details.append("[!] 未明确标注灌溉水利用系数，建议补充")
        score -= 15
    
    # 2. 灌水定额检查（正则匹配）
    quota_pattern = r"灌水定额[\s\S]*?(\d+\.?\d*)"
    m = re.search(quota_pattern, content)
    if m:
        val = float(m.group(1))
        if val > 80:
            details.append(f"[!] 灌水定额 {val}m³/亩 偏高，建议优化")
            score -= 10
        else:
            details.append(f"[✓] 灌水定额 {val}m³/亩 在合理范围内")
    
    # 3. 需水量计算检查（关键词匹配）
    if "ET0" in content or "et0" in content.lower() or "需水量" in content:
        details.append("[✓] 方案包含了需水量计算")
    
    # 4. 标准规范引用检查（关键词匹配）
    if "GB" in content or "SL" in content or "FAO" in content:
        details.append("[✓] 引用了相关标准规范")
    
    if score == 100:
        details.append("[✓] 合规性审查全部通过")
    
    return {"score": max(score, 0), "details": details}
```

### 第2步：合理性检查（权重30%）

调用 `_check_rationality(content, profile)`（`quality.py:180`），加分制：

```python
def _check_rationality(self, content: str, profile: Dict[str, Any] = None) -> Dict:
    details = []
    area = (profile or {}).get("area", 0)
    
    checks = [
        ("Penman-Monteith", "采用Penman-Monteith公式计算ET0", 20),
        ("FAO", "参考FAO-56标准", 15),
        ("Kc", "考虑作物系数Kc", 20),
        ("生育期", "分生育期制定灌溉方案", 15),
        ("灌水定额", "包含灌水定额计算", 15),
        ("灌水次数", "包含灌水频次安排", 15),
    ]
    
    total = 0
    for term, desc, weight in checks:
        if term.lower() in content.lower():
            details.append(f"[✓] {desc}")
            total += weight
        else:
            details.append(f"[!] 建议: {desc}")
    
    score = min(total, 100)
    
    if area < 100:
        score -= 10
        details.append("[!] 面积过小, 灌溉方案可能不经济")
    
    return {"score": max(score, 0), "details": details}
```

### 第3步：充分性检查（权重20%）

调用 `_check_sufficiency(content)`（`quality.py:209`），按章节存在性计分：

```python
def _check_sufficiency(self, content: str) -> Dict:
    details = []
    sections = {
        "项目概况": "项目基本信息",
        "灌溉制度": "灌溉制度设计",
        "设备": "设备选型清单",
        "说明": "方案说明与依据",
    }
    
    found = 0
    for kw, desc in sections.items():
        if kw in content:
            details.append(f"[✓] {desc}")
            found += 1
        else:
            details.append(f"[!] 缺失: {desc}")
    
    score = int(100 * found / len(sections))  # 4个章节，每个25分
    return {"score": score, "details": details}
```

### 第4步：知识库基准检查（权重15%）

调用 `_check_knowledge_benchmark(content, profile)`（`quality.py:61`），初始100分，缺失扣分：

```python
async def _check_knowledge_benchmark(self, content, profile):
    if self.knowledge_forest is None or profile is None:
        return None  # 知识森林不可用时返回None，上层调整权重
    
    crop = profile.get("crop_type", "")
    location = profile.get("location", "")
    
    # 两通道检索
    standard_results = await self.knowledge_forest.tagged_query(
        f"{crop} 灌溉 标准规范 技术指标 灌水定额", "standard", k=5
    )
    template_results = await self.knowledge_forest.tagged_query(
        f"{location} {crop} 灌溉规划 方案框架", "template", k=3
    )
    
    score = 100
    
    if standard_results:
        score -= 0  # 找到标准规范
        # 记录: "知识库检索到 X 条标准规范可交叉验证"
    else:
        score -= 15  # 未找到标准规范
    
    if template_results:
        score -= 0  # 找到方案模板
        # 记录: "知识库检索到 X 条方案模板可参照框架"
    else:
        score -= 10  # 缺少方案模板
    
    # 内容检查
    has_water_coef = any(kw in content for kw in ["灌溉水利用系数", "灌溉水有效利用系数"])
    has_quota = any(kw in content for kw in ["灌水定额"])
    has_standard_ref = any(kw in content for kw in ["GB", "SL", "FAO"])
    
    if has_water_coef: score -= 0; else: score -= 10
    if has_quota: score -= 0; else: score -= 5
    if has_standard_ref: score -= 0; else: score -= 5
    
    return {"score": min(score, 100), "details": details, 
            "standard_sources": [...], "template_sources": [...]}
```

### 第5步：加权计算

在 `check()` 方法中（`quality.py:20`）：

```python
compliance = self._check_compliance(plan_content, profile)
rationality = self._check_rationality(plan_content, profile)
sufficiency = self._check_sufficiency(plan_content)
kf_benchmark = await self._check_knowledge_benchmark(plan_content, profile)

if kf_benchmark:
    overall = int(0.35 * compliance["score"] + 0.30 * rationality["score"] + 
                  0.20 * sufficiency["score"] + 0.15 * kf_benchmark["score"])
else:
    overall = int(0.4 * compliance["score"] + 0.35 * rationality["score"] + 
                  0.25 * sufficiency["score"])
```

### 第6步：生成改进建议

从各维度的 `[!]` 标记中提取改进建议（`quality.py:33`）：

```python
suggestions = []
for d in compliance["details"] + rationality["details"] + sufficiency["details"]:
    if d.startswith("[!"):
        suggestions.append(d.replace("[!]", "").strip())
if kf_benchmark:
    for d in kf_benchmark.get("details", []):
        if d.startswith("[!"):
            suggestions.append(d.replace("[!]", "").strip())

if not suggestions:
    suggestions.append("方案整体质量良好，可直接使用")
```

## 决策标准

| 总分 | 等级 | 代码判断 |
|-----|------|---------|
| ≥ 90 | 优秀 | `_grade(90+) = "优秀"` |
| 75-89 | 良好 | `_grade(75-89) = "良好"` |
| 60-74 | 合格 | `_grade(60-74) = "合格"` |
| < 60 | 需改进 | `_grade(<60) = "需改进"` |

| 场景 | 决策 |
|-----|------|
| 知识森林不可用 | 跳过知识基准检查，权重调整为 合规性40%+合理性35%+充分性25% |
| 灌溉水利用系数 < 0.6 | 合规性扣20分 |
| 灌水定额 > 80 | 合规性扣10分 |
| 面积 < 100亩 | 合理性扣10分 |
| 缺少章节 | 充分性按缺失章节数扣分（每缺1章扣25分） |

## 工具使用

| 工具 | 调用方式 | 输入 | 输出 |
|-----|---------|------|------|
| `re.search()` | 本地正则 | 方案文本 | 参数值匹配 |
| `KnowledgeForest.tagged_query()` | async调用 | query, tag("standard"/"template"), k | 带标签的知识结果 |
| `json.dumps()` | 序列化 | 审核数据字典 | 日志输出 |

## 质量检查

在输出前确认：
- [ ] 四个维度全部执行完毕（知识基准可跳过返回None）
- [ ] 分数在 0~100 范围内（使用 `max(score, 0)` 和 `min(total, 100)`）
- [ ] 等级与分数匹配正确（≥90优秀, ≥75良好, ≥60合格, <60需改进）
- [ ] 改进建议来源于实际的 `[!]` 标记
- [ ] 权重总和为 1.0（有知识基准: 0.35+0.30+0.20+0.15=1.0；无知识基准: 0.40+0.35+0.25=1.0）
- [ ] suggestions 不为空（无建议时填充"方案整体质量良好，可直接使用"）

## 输出规范

```json
{
    "compliance": {"score": 100, "details": ["[✓] 灌溉水利用系数 0.85 ≥ 0.6...", "[✓] 合规性审查全部通过"]},
    "rationality": {"score": 100, "details": ["[✓] 采用Penman-Monteith公式计算ET0", ...]},
    "sufficiency": {"score": 100, "details": ["[✓] 项目基本信息", "[✓] 灌溉制度设计", "[✓] 设备选型清单", "[✓] 方案说明与依据"]},
    "knowledge_benchmark": {"score": 85, "details": ["[✓] 知识库检索到 3 条标准规范...", "[!] 方案未显式引用标准编号"], "standard_sources": [...], "template_sources": [...]},
    "overall_score": 95,
    "grade": "优秀",
    "suggestions": ["方案整体质量良好，可直接使用"]
}
```

> **代码位置**: `backend/agents/quality.py`
> **核心方法**: `check()`(line 20), `_check_compliance()`(line 139), `_check_rationality()`(line 180), `_check_sufficiency()`(line 209), `_check_knowledge_benchmark()`(line 61), `_grade()`(line 229)