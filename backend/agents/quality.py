from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
import re
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _log_json(tag: str, data: dict):
    logger.info(f"[质检] [{tag}] >>>\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}")


class QualityAgent:
    def __init__(self, db: Session, knowledge_forest: Optional[Any] = None):
        self.db = db
        self.knowledge_forest = knowledge_forest

    async def check(self, plan_content: str, profile: Dict[str, Any] = None) -> Dict[str, Any]:
        logger.info("[质检] 开始审核方案")

        compliance = self._check_compliance(plan_content, profile)
        rationality = self._check_rationality(plan_content, profile)
        sufficiency = self._check_sufficiency(plan_content)

        kf_benchmark = await self._check_knowledge_benchmark(plan_content, profile)
        if kf_benchmark:
            overall = int(0.35 * compliance["score"] + 0.30 * rationality["score"] + 0.20 * sufficiency["score"] + 0.15 * kf_benchmark["score"])
        else:
            overall = int(0.4 * compliance["score"] + 0.35 * rationality["score"] + 0.25 * sufficiency["score"])

        suggestions = []
        for d in compliance["details"]:
            if d.startswith("[!"):
                suggestions.append(d.replace("[!]", "").strip())
        for d in rationality["details"]:
            if d.startswith("[!"):
                suggestions.append(d.replace("[!]", "").strip())
        for d in sufficiency["details"]:
            if d.startswith("[!"):
                suggestions.append(d.replace("[!]", "").strip())
        if kf_benchmark:
            for d in kf_benchmark.get("details", []):
                if d.startswith("[!"):
                    suggestions.append(d.replace("[!]", "").strip())

        if not suggestions:
            suggestions.append("方案整体质量良好，可直接使用")

        return {
            "compliance": compliance,
            "rationality": rationality,
            "sufficiency": sufficiency,
            "overall_score": overall,
            "grade": self._grade(overall),
            "suggestions": suggestions,
            "knowledge_benchmark": kf_benchmark,
        }

    async def _check_knowledge_benchmark(
        self, content: str, profile: Dict[str, Any] = None
    ) -> Optional[Dict[str, Any]]:
        if self.knowledge_forest is None or profile is None:
            return None

        try:
            crop = profile.get("crop_type", "")
            location = profile.get("location", "")

            standard_results = await self.knowledge_forest.tagged_query(
                f"{crop} 灌溉 标准规范 技术指标 灌水定额", "standard", k=5
            )
            template_results = await self.knowledge_forest.tagged_query(
                f"{location} {crop} 灌溉规划 方案框架", "template", k=3
            )

            details = []
            score = 100

            if standard_results:
                standard_text = " ".join(r.get("content", "") for r in standard_results)
                details.append(f"[✓] 知识库检索到 {len(standard_results)} 条标准规范可交叉验证")
                standard_sources = list(set(r.get("source", "") for r in standard_results))
                details.append(f"[✓] 标准来源: {', '.join(standard_sources[:3])}")
            else:
                details.append("[!] 知识库中未找到直接适用的标准规范")
                score -= 15

            if template_results:
                details.append(f"[✓] 知识库检索到 {len(template_results)} 条方案模板可参照框架")
                template_sources = list(set(r.get("source", "") for r in template_results))
                details.append(f"[✓] 模板来源: {', '.join(template_sources[:3])}")
            else:
                details.append("[!] 知识库中缺少相似地区方案模板供参照")
                score -= 10

            has_water_coef = any(kw in content for kw in ["灌溉水利用系数", "灌溉水有效利用系数"])
            has_quota = any(kw in content for kw in ["灌水定额"])
            has_standard_ref = any(kw in content for kw in ["GB", "SL", "FAO"])

            if has_water_coef:
                details.append("[✓] 方案包含灌溉水利用系数")
            else:
                details.append("[!] 方案缺少灌溉水利用系数，不符合规范要求")
                score -= 10

            if has_quota:
                details.append("[✓] 方案包含灌水定额计算")
            else:
                details.append("[!] 方案缺少灌水定额参数")
                score -= 5

            if has_standard_ref:
                details.append("[✓] 方案引用了相关标准规范编号")
            else:
                details.append("[!] 方案未显式引用标准编号，建议补充")
                score -= 5

            _log_json("KNOWLEDGE_BENCHMARK", {
                "standard_count": len(standard_results),
                "template_count": len(template_results),
                "has_water_coef": has_water_coef,
                "has_quota": has_quota,
                "has_standard_ref": has_standard_ref,
                "score": min(score, 100),
            })

            return {
                "score": min(score, 100),
                "details": details,
                "standard_sources": [r.get("source", "") for r in standard_results],
                "template_sources": [r.get("source", "") for r in template_results],
            }
        except Exception as e:
            logger.warning(f"[质检] 知识库基准检查失败: {e}")
            return None

    def _check_compliance(self, content: str, profile: Dict[str, Any] = None) -> Dict:
        details = []
        score = 100

        area = (profile or {}).get("area", 0)
        crop = (profile or {}).get("crop_type", "")

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

        quota_pattern = r"灌水定额[\s\S]*?(\d+\.?\d*)"
        m = re.search(quota_pattern, content)
        if m:
            val = float(m.group(1))
            if val > 80:
                details.append(f"[!] 灌水定额 {val}m³/亩 偏高，建议优化")
                score -= 10
            else:
                details.append(f"[✓] 灌水定额 {val}m³/亩 在合理范围内")

        if "ET0" in content or "et0" in content.lower() or "需水量" in content:
            details.append("[✓] 方案包含了需水量计算")

        if "GB" in content or "SL" in content or "FAO" in content:
            details.append("[✓] 引用了相关标准规范")

        if score == 100:
            details.append("[✓] 合规性审查全部通过")

        return {"score": max(score, 0), "details": details}

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

        score = int(100 * found / len(sections))
        return {"score": score, "details": details}

    def _grade(self, score: int) -> str:
        if score >= 90:
            return "优秀"
        elif score >= 75:
            return "良好"
        elif score >= 60:
            return "合格"
        return "需改进"