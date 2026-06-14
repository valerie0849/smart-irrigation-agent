---
name: plan-modification
description: >
  灌溉方案局部修改技能。对已有方案进行定向修改，保持方案一致性和质量。
  通过LLM解析修改意图→BFS依赖展开→知识检索→骨架填充式生成→合并→审核→质量迭代。
  代码位于 backend/agents/modify.py，核心类：PlanModifyAgent，核心方法：modify()。
---

# 方案修改技能 — 定向修改与一致性保障

## 何时使用

当调度技能检测到参数微调（有历史方案+无新地点/作物+仅改预算等参数）或用户直接发出modify意图时，调用 `PlanModifyAgent(db).modify(original_plan, user_request)`。代码位置：`backend/agents/modify.py`。

## 领域知识

### 方案章节结构（`modify.py:12-13`）

```python
MODULE_ORDER = ["overview", "irrigation", "equipment", "references", "explanation"]
SECTION_NUMBERS = ["## 一", "## 二", "## 三", "## 四", "## 五"]
```

| 模块key | 章节标题 | 内容 |
|--------|---------|------|
| overview | `## 一、项目概况` | 项目基本信息、气象土壤数据 |
| irrigation | `## 二、灌溉制度设计` | ET0、Kc系数、灌水计划 |
| equipment | `## 三、设备选型清单` | 设备列表、成本核算 |
| references | `## 四、溯源依据` | 知识来源、标准引用 |
| explanation | `## 五、方案说明` | 方案总结与解释 |

### 章节依赖关系图（`modify.py:15-21`）

```python
DEPENDENCY_GRAPH = {
    "overview":    [],                    # 无依赖
    "irrigation":  ["overview"],          # 依赖项目概况
    "equipment":   ["irrigation", "overview"],  # 依赖灌溉设计+项目概况
    "references":  ["irrigation", "equipment"], # 依赖灌溉设计+设备选型
    "explanation": ["overview", "irrigation", "equipment"], # 依赖前三者
}
```

反向依赖自动计算（`modify.py:23-28`）：
```python
REVERSE_DEPS = {}
for mod, deps in DEPENDENCY_GRAPH.items():
    for dep in deps:
        REVERSE_DEPS.setdefault(dep, []).append(mod)
# 结果: REVERSE_DEPS = {"overview": ["irrigation","equipment","explanation"], "irrigation": ["equipment","references","explanation"], "equipment": ["references","explanation"]}
```

## 工作流程

### 入口方法

调用 `modify(original_plan, user_request)`（`modify.py:41`），执行8个步骤：

```python
async def modify(self, original_plan: str, user_request: str) -> Dict[str, Any]:
    modules = self._split_plan(original_plan)              # 预处理：拆分方案为模块
    intent = self._step1_parse_intent(user_request, original_plan)  # Step 1
    affected = self._step2_bfs_expand(intent.get("target_sections", []))  # Step 2
    knowledge_packs = await self._step3_retrieve(affected, user_request, modules)  # Step 3
    new_modules, quality_issues = self._step4_skeleton_generate(affected, modules, user_request, intent, knowledge_packs)  # Step 4
    merged = self._step5_merge_and_check(modules, new_modules, original_plan)  # Step 5
    quality, warnings = self._step6_audit(merged, new_modules, affected, original_plan)  # Step 6
    
    # Step 7: 质量迭代
    retry_info = {}
    if quality["overall_score"] < 75:
        retry_modules, retry_issues = self._step4_skeleton_generate(
            affected, modules, user_request, intent, knowledge_packs,
            quality_issues + quality.get("suggestions", [])
        )
        merged = self._step5_merge_and_check(modules, retry_modules, original_plan)
        new_modules.update(retry_modules)
        quality2, warnings2 = self._step6_audit(merged, retry_modules, affected, original_plan)
        quality = quality2
        retry_info = {"retried": True, "new_score": quality["overall_score"]}
    
    diff_data = self._generate_diff(original_plan, merged, modules, new_modules)  # Step 8
    krefs = self._collect_knowledge_refs(affected, knowledge_packs)
    if not krefs:
        krefs = self._fallback_knowledge_refs(affected, user_request)
    
    return {...}
```

### Step 1: LLM解析修改意图

调用 `_step1_parse_intent(user_request, plan_outline)`（`modify.py:151`）：

```python
def _step1_parse_intent(self, user_request, plan_outline):
    section_names = {
        "overview": "项目概况/现状与形势",
        "irrigation": "灌溉制度设计",
        "equipment": "设备选型清单",
        "references": "溯源依据",
        "explanation": "方案说明",
    }
    
    prompt = f"""你是灌溉方案修改解析器。将用户的修改指令解析为JSON。

## 方案章节结构
{sections}

## 用户修改指令
{user_request}

输出严格JSON（不要markdown标记）：
{{
  "operation": "update_budget|replace_method|add_section|delete_section|update_parameter",
  "target_sections": ["直接受影响的章节英文key列表"],
  "constraints": {{}},
  "dependency_hint": ["可能被连带影响的章节key"]
}}"""
    
    result = deepseek.generate(prompt, max_tokens=512)
    cleaned = result.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```\w*\n?|\n?```$", "", cleaned)  # 去除markdown代码块
    intent = json.loads(cleaned)
    return intent
```

**回退解析** `_fallback_parse(req)`（`modify.py:189`）：LLM失败时使用关键词匹配：

```python
def _fallback_parse(self, req):
    kw_map = {
        "预算": ["overview","equipment"], "投资": ["overview","equipment"],
        "面积": ["overview","irrigation"], "作物": ["overview","irrigation"],
        "滴灌": ["equipment","irrigation"], "喷灌": ["equipment","irrigation"],
        "传感器": ["equipment"], "水泵": ["equipment"],
        "灌水定额": ["irrigation"], "Kc": ["irrigation"], "ET0": ["irrigation"],
        "标准": ["references"], "规范": ["references"],
    }
    targets = set()
    for kw, mods in kw_map.items():
        if kw in req: targets.update(mods)
    if not targets: targets = {"overview", "explanation"}
    
    # 提取预算约束
    budget_m = re.search(r"预[算计]\s*(\d[\d,]*(?:\.\d+)?)(万|千|元)?", req)
    if budget_m:
        constraints["budget"] = budget_m.group(1) + (budget_m.group(2) or "元")
    
    return {"operation": "update_parameter", "target_sections": list(targets), "constraints": constraints}
```

### Step 2: BFS依赖展开

调用 `_step2_bfs_expand(targets)`（`modify.py:219`）：

```python
def _step2_bfs_expand(self, targets):
    affected = set()
    queue = list(targets)
    while queue:
        mod = queue.pop(0)
        if mod in affected or mod not in DEPENDENCY_GRAPH:
            continue
        affected.add(mod)
        for dep in DEPENDENCY_GRAPH.get(mod, []):       # 上游依赖
            if dep not in affected: queue.append(dep)
        for rev in REVERSE_DEPS.get(mod, []):           # 下游反向依赖
            if rev not in affected: queue.append(rev)
    
    ordered = [m for m in MODULE_ORDER if m in affected]  # 按MODULE_ORDER排序
    return set(ordered)
```

**示例**: targets=["equipment"] → affected={"overview", "irrigation", "equipment", "references", "explanation"}

### Step 3: 并行知识检索

调用 `_step3_retrieve(affected, user_request, modules)`（`modify.py:266`）：

```python
async def _step3_retrieve(self, affected, req, modules):
    forest = KnowledgeForest(self.db)
    if not forest.try_load(): return {}
    
    channel_queries = {
        "overview":    f"灌溉项目概况 投资构成 预算编制 {req[:30]}",
        "irrigation":  f"灌溉制度设计 灌水定额 Kc系数 Penman-Monteith {req[:30]}",
        "equipment":   f"灌溉设备选型 配置方案 成本核算 {req[:30]}",
        "references":  "灌溉标准规范 GB/T SL/T FAO 溯源依据",
        "explanation": f"灌溉方案说明 注意事项 {req[:30]}",
    }
    
    packs = {}
    for mod in affected:
        q = channel_queries.get(mod, req[:50])
        results = await forest.query(q, k=3)
        if results: packs[mod] = results
    return packs
```

### Step 4: 骨架-填充式分章生成

调用 `_step4_skeleton_generate(affected, modules, req, intent, knowledge, prev_issues)`（`modify.py:339`）：

```python
def _step4_skeleton_generate(self, affected, modules, req, intent, knowledge, prev_issues=None):
    new = {}
    quality_issues = []
    ordered = [m for m in MODULE_ORDER if m in affected]
    generated = dict(modules)
    
    issues_context = ""
    if prev_issues:
        issues_context = f"\n## 上次生成存在的问题（必须修复）\n" + "\n".join(f"- {issue}" for issue in prev_issues)
    
    for mod in ordered:
        old = modules.get(mod, "")
        ctx_before = self._adjacent_chapter(mod, -1, generated, modules)  # 上文章节
        ctx_after = self._adjacent_chapter(mod, +1, generated, modules)   # 下文章节
        ktext = self._fmt_knowledge(knowledge.get(mod, []))
        
        # 提取关键约束，突出显示
        constraint_text = ""
        constraints = intent.get('constraints', {})
        for k, v in constraints.items():
            if k == "budget":
                constraint_text += f"⚠️ **预算 = {v}**（必须将方案中所有预算相关数据更新为此值）\n"
            else:
                constraint_text += f"⚠️ **{k} = {v}**（必须使用此值，不能使用原值）\n"
        
        prompt = f"""你是灌溉方案专家。根据修改指令局部重写一个章节。

## ⚠️ 关键约束参数（必须严格遵守，优先级最高）
{constraint_text}

## 修改指令: {req}
{issues_context}

## 上文章节（保持衔接）: {ctx_before[:500] if ctx_before else "（无）"}
## 下文章节（保持衔接）: {ctx_after[:500] if ctx_after else "（无）"}
## 当前章节原内容: {old[:2000]}
## 参考知识: {ktext if ktext else "无额外知识，基于专业推断"}

## 生成要求
1. **必须严格执行约束参数和修改指令**，不能只输出原内容
2. 先输出该章节的1-2级小标题骨架，再逐节填充内容
3. 保持与原方案相同的编号层级和术语风格
4. 关键参数标注来源：`【来源：文档名】`
5. 章节标题严格不变，内容聚焦修改需求
6. 所有数据和参数必须基于参考知识或合理推断

只输出该章节的完整Markdown内容。"""
        
        result = deepseek.generate(prompt, max_tokens=3072)
        if result and result.strip() and len(result.strip()) > 20:
            new[mod] = result.strip()
            generated[mod] = result.strip()
        else:
            new[mod] = old  # 生成失败保留原文
            quality_issues.append(f"{mod}: LLM生成结果为空或过短，保留原文")
    
    return new, quality_issues
```

### Step 5: 合并方案

调用 `_step5_merge_and_check(original, new_modules, full_original_plan)`（`modify.py:433`）：

```python
def _step5_merge_and_check(self, original, new_modules, full_original_plan):
    result = full_original_plan
    
    for mod in MODULE_ORDER:
        old_content = original.get(mod, "")
        new_content = new_modules.get(mod, "")
        if not new_content or not old_content or old_content == new_content:
            continue
        
        # 策略1: 字符串精确匹配替换
        if old_content in result:
            result = result.replace(old_content, new_content, 1)
        else:
            # 策略2: 正则定位章节标题替换
            mod_idx = MODULE_ORDER.index(mod)
            num_prefix = SECTION_NUMBERS[mod_idx]
            pattern = re.escape(num_prefix) + r"[、.\s][^\n]*\n"
            m = re.search(pattern, result)
            if m:
                header_start, header_end = m.start(), m.end()
                original_header = result[header_start:header_end].strip()
                next_num = SECTION_NUMBERS[mod_idx + 1] if mod_idx + 1 < len(SECTION_NUMBERS) else None
                # 定位下一个章节标题，确定当前章节结束位置
                if next_num:
                    next_m = re.search(re.escape(next_num) + r"[、.\s][^\n]*\n", result[header_end:])
                    section_end = header_end + next_m.start() if next_m else len(result)
                else:
                    section_end = len(result)
                result = result[:header_start] + original_header + "\n" + new_content + "\n\n" + result[section_end:]
            else:
                # 策略3: 兜底追加到全文末尾
                result += "\n\n" + new_content
    
    result = self._update_cross_refs(result)  # 交叉引用检查
    return result
```

### Step 6: 局部质量审核

调用 `_step6_audit(full_plan, new_modules, affected, original_plan)`（`modify.py:508`）：

```python
def _step6_audit(self, full_plan, new_modules, affected, original_plan):
    local_combined = "\n".join(new_modules.get(m, "") for m in affected)
    
    # 合规性检查（初始100分）
    compliance_score = 100
    for label, pat in [("灌溉水利用系数", r"利用系数[\s\S]*?(\d+\.?\d*)"),
                        ("灌水定额", r"灌水定额[\s\S]*?(\d+\.?\d*)")]:
        if re.search(pat, local_combined): ... else: compliance_score -= 10
    
    if not any(kw in local_combined for kw in ["GB", "SL", "FAO"]):
        compliance_score -= 15
    
    # 合理性检查（加分制）
    rat_score = 0
    for term, desc, w in [("Penman-Monteith","PM公式",20), ("Kc","作物系数",20),
                           ("灌水定额","定额",15), ("灌水次数","频次",15)]:
        if term in local_combined: rat_score += w
    rat_score = min(rat_score, 100)
    
    local_overall = int(0.5 * compliance_score + 0.5 * rat_score)
    
    # 整体一致性检查
    consistency_warnings = self._overall_consistency_check(full_plan, original_plan)
    
    return {"overall_score": local_overall, "grade": ..., "compliance": ..., "rationality": ..., "suggestions": ...}, consistency_warnings
```

**整体一致性检查** `_overall_consistency_check(new_plan, old_plan)`（`modify.py:554`）：
```python
def _overall_consistency_check(self, new_plan, old_plan):
    warnings = []
    
    # 预算一致性
    budget_old = re.findall(r"预[算计][^\d]*(\d[\d,]*)", old_plan)
    budget_new = re.findall(r"预[算计][^\d]*(\d[\d,]*)", new_plan)
    if budget_old != budget_new and budget_new and budget_old:
        warnings.append(f"[!] 预算从{','.join(budget_old)}变更为{','.join(budget_new)}")
    
    # 灌溉方式一致性
    for method in ["滴灌", "喷灌", "沟灌"]:
        in_equip = method in new_plan
        has_equip_section = bool(re.search(method, new_plan[...]))  # 在"## 三"到"## 四"之间查找
        if in_equip and not has_equip_section:
            warnings.append(f"[!] {method}在方案中出现但设备清单未体现")
    
    # 方案长度检查
    if len(new_plan) < len(old_plan) * 0.7:
        warnings.append("[!] 修改后方案长度显著缩短，请检查是否丢失章节")
    
    return warnings
```

### Step 8: 生成Diff

调用 `_generate_diff(original, merged, orig_modules, new_modules)`（`modify.py:101`）：

```python
def _generate_diff(self, original, merged, orig_modules, new_modules):
    diffs = {}
    for mod in MODULE_ORDER:
        old_content = orig_modules.get(mod, "")
        new_content = new_modules.get(mod, "")
        if old_content and new_content and old_content != new_content:
            diffs[mod] = {
                "original": old_content,
                "modified": new_content,
                "changed": True,
                "change_summary": self._summarize_changes(old_content, new_content),
            }
    return diffs
```

变更摘要 `_summarize_changes(old, new)`（`modify.py:130`）：
```python
def _summarize_changes(self, old, new):
    old_words = set(re.findall(r"[\u4e00-\u9fff\w]+", old))
    new_words = set(re.findall(r"[\u4e00-\u9fff\w]+", new))
    added = new_words - old_words
    removed = old_words - new_words
    summary = []
    if added: summary.append(f"新增: {', '.join(list(added)[:5])}")
    if removed: summary.append(f"删除: {', '.join(list(removed)[:5])}")
    old_lines, new_lines = len(old.splitlines()), len(new.splitlines())
    if new_lines > old_lines: summary.append(f"内容增加{new_lines - old_lines}行")
    elif new_lines < old_lines: summary.append(f"内容减少{old_lines - new_lines}行")
    return "; ".join(summary) if summary else "内容已更新"
```

### 兜底知识依据

当知识森林无返回结果时（`modify.py:309`）：
```python
def _fallback_knowledge_refs(self, affected, req):
    fallback = []
    for mod in affected:
        name = section_names.get(mod, mod)
        fallback.append({
            "module": mod, "source": "智慧灌溉方案模板库",
            "content": f"根据用户修改指令「{req[:80]}」对 {name} 章节进行定向修改，参考了灌溉设计规范 GB/T 50363-2018 和 SL 287-2014 标准。",
            "similarity": 0.85, "tag": "template",
        })
        fallback.append({
            "module": mod, "source": "水利灌溉设计标准",
            "content": "灌溉水利用系数不低于0.55，管道输水水利用系数不低于0.95...",
            "similarity": 0.82, "tag": "standard",
        })
    return fallback
```

## 决策标准

| 场景 | 决策 |
|-----|------|
| LLM解析意图失败 | 使用关键词回退解析（`_fallback_parse`） |
| 知识森林不可用 | 使用兜底模板知识依据（`_fallback_knowledge_refs`） |
| 章节标题正则不匹配 | 兜底追加到方案末尾 |
| LLM生成结果过短（≤20字符） | 保留原章节内容 |
| 质量评分 ≥ 75 | 正常返回 |
| 质量评分 < 75 | 自动重试一次，将问题作为修正指令 |
| 重试后仍 < 75 | 返回当前结果 + `retry_info={"retried": True, "new_score": ...}` |

## 工具使用

| 工具 | 调用方式 | 输入 | 输出 |
|-----|---------|------|------|
| `deepseek.generate()` | 直接调用 | prompt, max_tokens=512/3072 | generated_text |
| `KnowledgeForest.query()` | async调用 | query字符串, k=3 | 知识检索结果列表 |
| `re.search()`/`re.sub()` | 本地正则 | 方案文本 | 章节定位、参数提取、意图解析 |
| BFS算法 | 本地计算 | target_sections列表 | 受影响章节集合 |

## 质量检查

在输出前确认：
- [ ] 受影响章节全部更新（按BFS展开结果检查）
- [ ] 新方案长度 ≥ 原方案长度 × 0.7
- [ ] 预算数据在全文一致（overview、equipment统一）
- [ ] 灌溉方式与设备清单一致（喷灌对应喷头，滴灌对应滴灌带）
- [ ] 质量评分 ≥ 60（低于60需人工介入）
- [ ] diff 数据包含所有实际修改的章节
- [ ] knowledge_refs 不为空（知识森林失败时使用兜底）

## 输出规范

```json
{
    "content": "# 智慧灌溉方案报告...",
    "original_plan": "...",
    "quality": {"overall_score": 85, "grade": "良好", "compliance": {...}, "rationality": {...}, "suggestions": [...]},
    "warnings": ["[!] 预算从200万变更为300万，请确认其他费用章节同步"],
    "affected_sections": ["equipment", "overview", "explanation"],
    "knowledge_refs": [{"module": "equipment", "source": "文献1.pdf", "content": "...", "similarity": 0.85}],
    "diff": {"equipment": {"original": "...", "modified": "...", "changed": true, "change_summary": "新增: 300; 预算更新"}},
    "retry_info": {"retried": true, "new_score": 88},
    "new_modules": {"overview": "...", "equipment": "...", "explanation": "..."}
}
```

> **代码位置**: `backend/agents/modify.py`
> **核心方法**: `modify()`(line 41), `_step1_parse_intent()`(line 151), `_fallback_parse()`(line 189), `_step2_bfs_expand()`(line 219), `_split_plan()`(line 241), `_step3_retrieve()`(line 266), `_step4_skeleton_generate()`(line 339), `_step5_merge_and_check()`(line 433), `_step6_audit()`(line 508), `_overall_consistency_check()`(line 554), `_generate_diff()`(line 101), `_summarize_changes()`(line 130), `_fallback_knowledge_refs()`(line 309)