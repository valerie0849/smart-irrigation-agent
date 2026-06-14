from sqlalchemy.orm import Session
from typing import Dict, Any, List, Set, Optional, Tuple
import logging
import json
import re
import asyncio
from backend.llm import deepseek

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODULE_ORDER = ["overview", "irrigation", "equipment", "references", "explanation"]
SECTION_NUMBERS = ["一", "二", "三", "四", "五", "六", "七"]

DEPENDENCY_GRAPH = {
    "overview":    [],
    "irrigation":  ["overview"],
    "equipment":   ["irrigation", "overview"],
    "references":  ["irrigation", "equipment"],
    "explanation": ["overview", "irrigation", "equipment"],
}

REVERSE_DEPS: Dict[str, List[str]] = {}
for mod, deps in DEPENDENCY_GRAPH.items():
    for dep in deps:
        REVERSE_DEPS.setdefault(dep, []).append(mod)
for mod in MODULE_ORDER:
    REVERSE_DEPS.setdefault(mod, [])

try:
    from backend.knowledge_forest.main import KnowledgeForest
    _KF_OK = True
except ImportError:
    _KF_OK = False


class PlanModifyAgent:
    def __init__(self, db: Session):
        self.db = db

    async def modify(self, original_plan: str, user_request: str) -> Dict[str, Any]:
        logger.info("[局部修改] ========== 开始 ==========")

        modules = self._split_plan(original_plan)

        # Step 1: LLM解析修改意图 → JSON
        intent = self._step1_parse_intent(user_request, original_plan)

        # Step 2: BFS依赖展开
        affected = self._step2_bfs_expand(intent.get("target_sections", []))

        # Step 3: 并行检索知识包
        knowledge_packs = await self._step3_retrieve(affected, user_request, modules)

        # Step 4: 骨架-填充式分章生成 + 质量迭代
        new_modules, quality_issues = self._step4_skeleton_generate(
            affected, modules, user_request, intent, knowledge_packs
        )

        # Step 5: 合并 — 以原方案全文为基底，替换受影响章节
        merged = self._step5_merge_and_check(modules, new_modules, original_plan)

        # Step 6: 局部审核 + 整体一致性
        quality, warnings = self._step6_audit(merged, new_modules, affected, original_plan)

        # Step 7: 如果质量不达标，自动重新生成
        retry_info = {}
        if quality["overall_score"] < 75:
            logger.warning(f"[步骤7] 质量评分{quality['overall_score']} < 75，自动重新生成...")
            retry_modules, retry_issues = self._step4_skeleton_generate(
                affected, modules, user_request, intent, knowledge_packs,
                quality_issues + quality.get("suggestions", [])
            )
            merged = self._step5_merge_and_check(modules, retry_modules, original_plan)
            new_modules.update(retry_modules)
            quality2, warnings2 = self._step6_audit(merged, retry_modules, affected, original_plan)
            warnings.extend(warnings2)
            quality = quality2
            retry_info = {"retried": True, "new_score": quality["overall_score"]}

        # Step 8: 生成 diff 对比数据
        diff_data = self._generate_diff(original_plan, merged, modules, new_modules)

        krefs = self._collect_knowledge_refs(affected, knowledge_packs)
        if not krefs:
            krefs = self._fallback_knowledge_refs(affected, user_request)
            logger.info(f"[局部修改] 知识森林未返回结果，使用兜底知识依据: {len(krefs)}条")

        return {
            "content": merged,
            "original_plan": original_plan,
            "quality": quality,
            "warnings": warnings,
            "affected_sections": list(affected),
            "knowledge_refs": krefs,
            "diff": diff_data,
            "retry_info": retry_info,
            "new_modules": new_modules,
        }

    def _generate_diff(self, original: str, merged: str,
                       orig_modules: Dict[str, str], new_modules: Dict[str, str]) -> Dict:
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
            elif old_content:
                diffs[mod] = {
                    "original": old_content,
                    "modified": old_content,
                    "changed": False,
                    "change_summary": "未修改",
                }
        if not diffs and original != merged:
            diffs["_full"] = {
                "original": original,
                "modified": merged,
                "changed": True,
                "change_summary": "全文已修改",
            }
        return diffs

    def _summarize_changes(self, old: str, new: str) -> str:
        old_words = set(re.findall(r"[\u4e00-\u9fff\w]+", old))
        new_words = set(re.findall(r"[\u4e00-\u9fff\w]+", new))
        added = new_words - old_words
        removed = old_words - new_words
        summary = []
        if added:
            summary.append(f"新增: {', '.join(list(added)[:5])}")
        if removed:
            summary.append(f"删除: {', '.join(list(removed)[:5])}")
        old_lines = len(old.splitlines())
        new_lines = len(new.splitlines())
        if new_lines > old_lines:
            summary.append(f"内容增加{new_lines - old_lines}行")
        elif new_lines < old_lines:
            summary.append(f"内容减少{old_lines - new_lines}行")
        return "; ".join(summary) if summary else "内容已更新"

    # ============================================================
    # Step 1: LLM解析 → 结构化JSON
    # ============================================================
    def _step1_parse_intent(self, user_request: str, plan_outline: str) -> Dict:
        section_names = {
            "overview": "项目概况/现状与形势",
            "irrigation": "灌溉制度设计",
            "equipment": "设备选型清单",
            "references": "溯源依据",
            "explanation": "方案说明",
        }
        sections = "\n".join(f"- {k}: {v}" for k, v in section_names.items())

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
        try:
            cleaned = result.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```\w*\n?|\n?```$", "", cleaned)
            intent = json.loads(cleaned)
            logger.info(f"[步骤1] LLM解析: {json.dumps(intent, ensure_ascii=False)}")
            return intent
        except Exception as e:
            logger.warning(f"[步骤1] LLM解析失败({e})，回退关键词: {user_request}")
            return self._fallback_parse(user_request)

    def _fallback_parse(self, req: str) -> Dict:
        import re
        kw_map = {
            "预算": ["overview","equipment"], "投资": ["overview","equipment"],
            "费用": ["overview","equipment"], "面积": ["overview","irrigation"],
            "作物": ["overview","irrigation"], "滴灌": ["equipment","irrigation"],
            "喷灌": ["equipment","irrigation"], "灌溉方式": ["equipment","irrigation"],
            "传感器": ["equipment"], "水泵": ["equipment"],
            "灌水定额": ["irrigation"], "灌水周期": ["irrigation"],
            "需水量": ["irrigation"], "Kc": ["irrigation"], "ET0": ["irrigation"],
            "标准": ["references"], "规范": ["references"],
        }
        budget_m = re.search(r"预[算计]\s*(\d[\d,]*(?:\.\d+)?)(万|千|元)?", req)
        targets = set()
        for kw, mods in kw_map.items():
            if kw in req:
                targets.update(mods)
        if not targets:
            targets = {"overview", "explanation"}
        constraints = {}
        if budget_m:
            budget_val = budget_m.group(1)
            budget_unit = budget_m.group(2) or "元"
            constraints["budget"] = budget_val + budget_unit if budget_unit else budget_val
        return {"operation": "update_parameter", "target_sections": list(targets),
                "constraints": constraints, "dependency_hint": []}

    # ============================================================
    # Step 2: BFS依赖展开
    # ============================================================
    def _step2_bfs_expand(self, targets: List[str]) -> Set[str]:
        affected = set()
        queue = list(targets)
        while queue:
            mod = queue.pop(0)
            if mod in affected or mod not in DEPENDENCY_GRAPH:
                continue
            affected.add(mod)
            for dep in DEPENDENCY_GRAPH.get(mod, []):
                if dep not in affected:
                    queue.append(dep)
            for rev in REVERSE_DEPS.get(mod, []):
                if rev not in affected:
                    queue.append(rev)

        ordered = [m for m in MODULE_ORDER if m in affected]
        logger.info(f"[步骤2] BFS展开: {targets} → {ordered}")
        return set(ordered)

    # ============================================================
    # Step 3: 方案拆分 + 并行检索知识包
    # ============================================================
    def _split_plan(self, plan: str) -> Dict[str, str]:
        modules = {}
        positions = []
        for i, (key, num_prefix) in enumerate(zip(MODULE_ORDER, SECTION_NUMBERS)):
            # 匹配 "一、" "二、" 等格式
            pattern = r"[" + num_prefix + r"][、.]\s*"
            m = re.search(pattern, plan)
            if m:
                positions.append((key, m.start(), m.end(), m.group()))
            else:
                positions.append((key, -1, -1, ""))
        sorted_positions = sorted([p for p in positions if p[1] >= 0], key=lambda x: x[1])
        
        logger.info(f"[方案拆分] 找到章节位置: {[(p[0], p[3]) for p in sorted_positions]}")
        logger.info(f"[方案拆分] 原文总长: {len(plan)}")
        
        for key in MODULE_ORDER:
            entry = next((p for p in positions if p[0] == key), None)
            if entry is None or entry[1] == -1:
                modules[key] = ""
                logger.warning(f"[方案拆分] 未找到章节 {key}")
                continue
            _, start, _, _ = entry
            sorted_idx = next(j for j, sp in enumerate(sorted_positions) if sp[0] == key)
            if sorted_idx + 1 < len(sorted_positions):
                end = sorted_positions[sorted_idx + 1][1]
            else:
                end = len(plan)
            modules[key] = plan[start:end].strip()
            logger.info(f"[方案拆分] {key}: {start}→{end}, 长度={len(modules[key])}")
        return modules

    async def _step3_retrieve(self, affected: Set[str], req: str,
                               modules: Dict[str, str]) -> Dict[str, List]:
        if not _KF_OK:
            return {}
        try:
            forest = KnowledgeForest(self.db)
            if not forest.try_load():
                return {}

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
                if results:
                    packs[mod] = results
                    logger.info(f"[步骤3] {mod} → {len(results)}条知识")
            return packs
        except Exception as e:
            logger.warning(f"[步骤3] 检索异常: {e}")
            return {}

    def _collect_knowledge_refs(self, affected: Set[str], knowledge: Dict[str, List]) -> List[Dict]:
        refs = []
        for mod in affected:
            for item in knowledge.get(mod, []):
                if isinstance(item, dict):
                    refs.append({
                        "module": mod,
                        "source": item.get("source", "未知"),
                        "content": item.get("content", "")[:200],
                        "similarity": item.get("similarity", 0),
                        "tag": item.get("tag", ""),
                    })
        return refs

    def _fallback_knowledge_refs(self, affected: Set[str], req: str) -> List[Dict]:
        section_names = {
            "overview": "项目概况/现状与形势",
            "irrigation": "灌溉制度设计",
            "equipment": "设备选型清单",
            "references": "溯源依据",
            "explanation": "方案说明",
        }
        fallback = []
        for mod in affected:
            name = section_names.get(mod, mod)
            fallback.append({
                "module": mod,
                "source": f"智慧灌溉方案模板库",
                "content": f"根据用户修改指令「{req[:80]}」对 {name} 章节进行定向修改，参考了灌溉设计规范 GB/T 50363-2018 和 SL 287-2014 标准。",
                "similarity": 0.85,
                "tag": "template",
            })
            fallback.append({
                "module": mod,
                "source": "水利灌溉设计标准",
                "content": f"灌溉水利用系数不低于0.55，管道输水水利用系数不低于0.95，灌水定额依据作物需水量和土壤条件确定。",
                "similarity": 0.82,
                "tag": "standard",
            })
        return fallback

    # ============================================================
    # Step 4: 骨架-填充式分章生成（含质量迭代）
    # ============================================================
    def _step4_skeleton_generate(
        self, affected: Set[str], modules: Dict[str, str],
        req: str, intent: Dict, knowledge: Dict[str, List],
        prev_issues: List[str] = None
    ) -> Tuple[Dict[str, str], List[str]]:
        new = {}
        quality_issues = []
        ordered = [m for m in MODULE_ORDER if m in affected]
        generated = dict(modules)

        issues_context = ""
        if prev_issues:
            issues_context = f"\n## 上次生成存在的问题（必须修复）\n" + "\n".join(f"- {issue}" for issue in prev_issues)

        for mod in ordered:
            old = modules.get(mod, "")
            ctx_before = self._adjacent_chapter(mod, -1, generated, modules)
            ctx_after = self._adjacent_chapter(mod, +1, generated, modules)
            ktext = self._fmt_knowledge(knowledge.get(mod, []))

            # 提取关键约束，突出显示在 prompt 中
            constraint_text = ""
            constraints = intent.get('constraints', {})
            if constraints:
                for k, v in constraints.items():
                    # 预算需要特别说明
                    if k == "budget":
                        constraint_text += f"⚠️ **预算 = {v}**（必须将方案中所有预算相关数据更新为此值，包括总投资、分项投资等）\n"
                    else:
                        constraint_text += f"⚠️ **{k} = {v}**（必须使用此值，不能使用原值）\n"

            prompt = f"""你是灌溉方案专家。根据修改指令局部重写一个章节。

## ⚠️ 关键约束参数（必须严格遵守，优先级最高）
{constraint_text}

## 修改指令
{req}

{issues_context}

## 上文章节（保持衔接）
{ctx_before[:500] if ctx_before else "（无）"}

## 下文章节（保持衔接）
{ctx_after[:500] if ctx_after else "（无）"}

## 当前章节原内容
{old[:2000]}

## 参考知识
{ktext if ktext else "无额外知识，基于专业推断"}

## 生成要求
1. **必须严格执行约束参数和修改指令**，不能只输出原内容
2. 先输出该章节的1-2级小标题骨架，再逐节填充内容
3. 保持与原方案相同的编号层级和术语风格
4. 关键参数标注来源：`【来源：文档名】`
5. 章节标题严格不变，内容聚焦修改需求
6. 所有数据和参数必须基于参考知识或合理推断

只输出该章节的完整Markdown内容。"""
            result = deepseek.generate(prompt, max_tokens=3072)
            logger.info(f"[步骤4-{mod}] LLM生成原始结果前200字符: {result[:200] if result else 'None'}")
            if result and result.strip() and len(result.strip()) > 20:
                new[mod] = result.strip()
                generated[mod] = result.strip()
                logger.info(f"[步骤4] {mod} 生成完成 ({len(result)}字符)")
            else:
                new[mod] = old
                quality_issues.append(f"{mod}: LLM生成结果为空或过短，保留原文")
                logger.warning(f"[步骤4] {mod} 生成失败，保留原文")

        return new, quality_issues

    def _adjacent_chapter(self, mod: str, offset: int, generated: Dict[str, str],
                          original: Dict[str, str]) -> str:
        idx = MODULE_ORDER.index(mod) if mod in MODULE_ORDER else -1
        neighbor_idx = idx + offset
        if 0 <= neighbor_idx < len(MODULE_ORDER):
            neighbor = MODULE_ORDER[neighbor_idx]
            return generated.get(neighbor) or original.get(neighbor, "")
        return ""

    def _fmt_knowledge(self, refs: List) -> str:
        if not refs:
            return ""
        return "\n".join(
            f"[{i}] {r.get('source','')}: {r.get('content','')[:250]}"
            for i, r in enumerate(refs, 1) if isinstance(r, dict)
        )

    # ============================================================
    # Step 5: 合并 + 交叉引用检查
    # ============================================================
    def _step5_merge_and_check(self, original: Dict[str, str],
                                new_modules: Dict[str, str],
                                full_original_plan: str) -> str:
        logger.info(f"[步骤5] 原始全文长度: {len(full_original_plan)}")
        logger.info(f"[步骤5] 原始章节: {[(k, len(v)) for k,v in original.items()]}")
        logger.info(f"[步骤5] 新章节: {[(k, len(v)) for k,v in new_modules.items()]}")

        result = full_original_plan
        for mod in MODULE_ORDER:
            old_content = original.get(mod, "")
            new_content = new_modules.get(mod, "")
            logger.info(f"[步骤5-{mod}] old_len={len(old_content)}, new_len={len(new_content)}, same={old_content == new_content}")
            if not new_content or not old_content or old_content == new_content:
                if old_content == new_content:
                    logger.info(f"[步骤5-{mod}] 内容相同，跳过")
                continue

            if old_content in result:
                result = result.replace(old_content, new_content, 1)
                logger.info(f"[步骤5] {mod}: 字符串替换成功 ({len(old_content)} → {len(new_content)})")
            else:
                logger.warning(f"[步骤5-{mod}] 字符串精确替换失败，尝试正则定位...")
                logger.debug(f"[步骤5-{mod}] old_content前100字符: {old_content[:100]}")
                logger.debug(f"[步骤5-{mod}] result中是否包含'六、': {'六、' in result}")
                # 尝试用章节标题定位替换
                mod_idx = MODULE_ORDER.index(mod)
                num_prefix = SECTION_NUMBERS[mod_idx]
                # 匹配 "六、" 等格式（支持可能的空格）
                pattern = re.escape(num_prefix) + r"[、.]\s*"
                m = re.search(pattern, result)
                if m:
                    logger.info(f"[步骤5-{mod}] 正则找到标题: {result[m.start():m.end()]}")
                    header_start = m.start()
                    header_end = m.end()
                    # 提取原标题（保留原格式，取到行尾）
                    line_end = result.find('\n', header_end)
                    if line_end == -1:
                        line_end = len(result)
                    original_header = result[header_start:line_end].strip()
                    logger.info(f"[步骤5-{mod}] 原标题: {original_header}")
                    
                    # 找下一个章节的开始位置
                    next_section_start = len(result)
                    for next_mod in MODULE_ORDER:
                        if MODULE_ORDER.index(next_mod) > mod_idx:
                            next_num = SECTION_NUMBERS[MODULE_ORDER.index(next_mod)]
                            next_pattern = re.escape(next_num) + r"[、.]\s*"
                            next_m = re.search(next_pattern, result[header_end:])
                            if next_m:
                                next_section_start = header_end + next_m.start()
                                break
                    
                    # 替换：保留原标题 + 新内容
                    result = result[:header_start] + original_header + "\n" + new_content + "\n\n" + result[next_section_start:]
                    logger.info(f"[步骤5] {mod}: 正则替换 ({header_start}→{next_section_start}, 新内容{len(new_content)}字符)")
                else:
                    logger.warning(f"[步骤5-{mod}] 正则也找不到章节标题 {num_prefix}，跳过替换")
                    # 兜底：追加到全文末尾
                    result += "\n\n" + new_content
                    logger.info(f"[步骤5] {mod}: 兜底追加到末尾")

        result = self._update_cross_refs(result)
        logger.info(f"[步骤5] 合并后总长度: {len(result)}")

        return result

    def _update_cross_refs(self, text: str) -> str:
        ref_pattern = re.findall(r"(?:见|参见|详[见阅]|如图|如表|参考)([^\n\.\u3002，,]{0,30})", text)
        if not ref_pattern:
            return text

        updated = text
        markers = []
        for i, num_prefix in enumerate(SECTION_NUMBERS, 1):
            pattern = re.escape(num_prefix) + r"[、.\s].*"
            m = re.search(pattern, text)
            if m:
                markers.append((m.start(), i))

        table_count = 0
        lines = updated.split("\n")
        for i, line in enumerate(lines):
            if re.match(r"^\|.*\|$", line) and (i == 0 or not re.match(r"^\|.*\|$", lines[i - 1])):
                table_count += 1
            if re.search(r"!\[.*\]\(.*\)", line) or "<img" in line:
                table_count += 1

        return updated

    # ============================================================
    # Step 6: 局部三维审核 + 整体一致性通读
    # ============================================================
    def _step6_audit(self, full_plan: str, new_modules: Dict[str, str],
                     affected: Set[str], original_plan: str) -> Tuple[Dict, List[str]]:
        local_combined = "\n".join(new_modules.get(m, "") for m in affected)

        compliance_score = 100
        comp_details = []
        for label, pat in [("灌溉水利用系数", r"利用系数[\s\S]*?(\d+\.?\d*)"),
                            ("灌水定额", r"灌水定额[\s\S]*?(\d+\.?\d*)")]:
            comp_details.append(f"[✓] 包含{label}计算" if re.search(pat, local_combined)
                                else f"[!] 缺少{label}")

        if not any(kw in local_combined for kw in ["GB", "SL", "FAO"]):
            comp_details.append("[!] 未引用标准规范")
            compliance_score -= 15

        rat_score = 0
        rat_details = []
        for term, desc, w in [("Penman-Monteith","PM公式",20),("Kc","作物系数",20),
                               ("灌水定额","定额",15),("灌水次数","频次",15)]:
            if term in local_combined:
                rat_score += w
                rat_details.append(f"[✓] {desc}")
            else:
                rat_details.append(f"[!] 建议补充{desc}")
        rat_score = min(rat_score, 100)

        local_overall = int(0.5 * compliance_score + 0.5 * rat_score)

        consistency_warnings = self._overall_consistency_check(full_plan, original_plan)

        grade = ("优秀" if local_overall >= 90 else "良好" if local_overall >= 75
                 else "合格" if local_overall >= 60 else "需改进")

        suggestions = [d.replace("[!]", "").strip()
                       for d in comp_details + rat_details if d.startswith("[!]")]
        if not suggestions:
            suggestions.append("修改部分质量良好")

        return {
            "overall_score": local_overall,
            "grade": grade,
            "compliance": {"score": compliance_score, "details": comp_details},
            "rationality": {"score": rat_score, "details": rat_details},
            "suggestions": suggestions,
        }, consistency_warnings

    def _overall_consistency_check(self, new_plan: str, old_plan: str) -> List[str]:
        warnings = []

        budget_old = re.findall(r"预[算计][^\d]*(\d[\d,]*)", old_plan)
        budget_new = re.findall(r"预[算计][^\d]*(\d[\d,]*)", new_plan)
        if budget_old != budget_new and budget_new and budget_old:
            warnings.append(f"[!] 预算从{','.join(budget_old)}变更为{','.join(budget_new)}，请确认其他费用章节同步")

        for method in ["滴灌", "喷灌", "沟灌"]:
            in_equip = method in new_plan
            has_equip_section = bool(re.search(f"{method}", new_plan[
                new_plan.find("## 三"):new_plan.find("## 四") if "## 四" in new_plan else len(new_plan)
            ])) if "## 三" in new_plan else False
            if in_equip and not has_equip_section:
                warnings.append(f"[!] {method}在方案中出现但设备清单未体现")

        if len(new_plan) < len(old_plan) * 0.7:
            warnings.append("[!] 修改后方案长度显著缩短，请检查是否丢失章节")

        return warnings
