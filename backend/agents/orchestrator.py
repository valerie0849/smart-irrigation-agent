from sqlalchemy.orm import Session
from typing import Dict, Any, List
from jinja2 import Environment, BaseLoader
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OrchestratorAgent:
    def __init__(self, db: Session):
        self.db = db

    async def orchestrate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        profile = data.get("project_profile", {})
        irrigation_plan = data.get("irrigation_plan", {})
        equipment_list = data.get("equipment_list", {})
        sources = data.get("sources", [])
        knowledge_refs = data.get("knowledge_refs", [])

        design_knowledge = irrigation_plan.get("knowledge_learned", [])
        equip_knowledge = equipment_list.get("knowledge_sources", [])
        all_learned = self._merge_knowledge(design_knowledge, knowledge_refs, equip_knowledge)

        content = self._generate_report(profile, irrigation_plan, equipment_list, sources, all_learned)
        explanation = self._generate_explanation(profile, irrigation_plan, all_learned)

        return {"content": content, "explanation": explanation, "sources": sources}

    def _merge_knowledge(
        self, design: List, general: List, equip: List
    ) -> List[Dict[str, Any]]:
        seen = set()
        merged = []
        for item in design + general + equip:
            if isinstance(item, dict):
                key = item.get("source", "") + item.get("content", "")[:80]
            elif isinstance(item, str):
                key = item
            else:
                key = str(item)
            if key not in seen:
                seen.add(key)
                merged.append(item)
        return merged

    def _generate_report(
        self,
        profile: Dict,
        plan: Dict,
        equip: Dict,
        sources: List,
        knowledge_refs: List,
    ) -> str:
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

    def _generate_explanation(self, profile: Dict, plan: Dict, knowledge: List = None) -> str:
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
                lines.append(f"参考知识源: {', '.join(sorted(k_sources)[:3])}")

        return "\n".join(lines)


REPORT_TEMPLATE = r"""
# 智慧灌溉方案报告

## 一、项目概况

### 1.1 项目基本信息
- **项目地点**：{{ location }}
- **作物类型**：{{ crop_type }}{% if crop_inferred %} *（根据地区自动推断）*{% endif %}
- **灌溉面积**：{{ area }} 亩
- **预算规模**：{{ "{:,.2f}".format(budget) }} 元

### 1.2 气象条件（实时数据 - {{ weather.get('source', 'Open-Meteo') }}）
- **经纬度**：({{ weather.get('latitude', '--') }}, {{ weather.get('longitude', '--') }})
- **平均气温**：{{ weather.get('temperature', '--') }}℃
- **相对湿度**：{{ weather.get('humidity', '--') }}%
- **参考蒸散发 ET0**：{{ weather.get('et0', '--') }} mm/天

### 1.3 土壤条件（实时数据 - {{ soil.get('source', 'Open-Meteo') }}）
- **土壤类型**：{{ soil.get('soil_type', '--') }}
- **田间持水量**：{{ "{:.1%}".format(soil.get('field_capacity', 0)) }}
- **萎蔫系数**：{{ "{:.1%}".format(soil.get('wilting_point', 0)) }}
- **有效含水量**：{{ "{:.1%}".format(soil.get('available_water', 0)) }}

---

## 二、灌溉制度设计

### 2.1 设计参数
- **计算方法**：Penman-Monteith 公式（FAO-56）
- **参考作物需水量 ET0**：{{ calc.get('et0', '--') }} mm/天
- **灌溉方式**：{{ proj.get('irrigation_method', '--') }}
- **总需水量**：{{ "{:,.2f}".format(proj.get('total_water_requirement_m3', 0)) }} m³
- **灌溉水利用系数**：0.85

### 2.2 生育期灌溉方案

| 生育阶段 | 天数 | Kc | 日均需水(mm) | 阶段需水(mm) | 灌水定额(mm) | 灌水次数 |
|----------|------|----|-----------|----------|----------|---------|
{% for p in schedule %}
| {{ p.stage }} | {{ p.duration_days }} | {{ p.kc_value }} | {{ p.etc_mm_per_day }} | {{ p.water_requirement_mm }} | {{ p.irrigation_amount_mm }} | {{ p.number_of_irrigations }} |
{% endfor %}

---

## 三、设备选型清单

| 设备名称 | 型号 | 数量 | 单价(元) | 小计(元) |
|----------|------|------|----------|----------|
{% for e in eq_list %}
| {{ e.name }} | {{ e.model }} | {{ e.quantity }}{{ e.unit }} | {{ "{:.2f}".format(e.price) }} | {{ "{:.2f}".format(e.price * e.quantity) }} |
{% endfor %}

- **设备总投资**：{{ "{:,.2f}".format(eq_total) }} 元
- **预算金额**：{{ "{:,.2f}".format(budget) }} 元
- **预算结余**：{{ "{:,.2f}".format(budget - eq_total) }} 元

---

## 四、溯源依据

### 4.1 知识森林参考来源
{% if krefs %}
{% for ref in krefs %}
{% if ref is mapping %}
- **来源**：`{{ ref.get('source', '未知') }}` | **相似度**：{{ "{:.2%}".format(ref.get('similarity', 0)) }}
  > {{ ref.get('content', '')[:120] }}{% if ref.get('content', '')|length > 120 %}...{% endif %}
{% else %}
- {{ ref[:120] }}{% if ref|length > 120 %}...{% endif %}
{% endif %}
{% endfor %}
{% else %}
暂无知识森林参考来源
{% endif %}

### 4.2 参考标准与文献
{% if sources %}
{% for s in sources %}
- {{ s }}
{% endfor %}
{% else %}
- FAO-56: Crop Evapotranspiration - Guidelines for Computing Crop Water Requirements
- GB/T 50363-2018《旱作节水灌溉工程技术规范》
- GB/T 50085-2017《喷灌工程技术规范》
- SL/T 699-2025《灌溉水有效利用系数测定技术导则》
{% endif %}

---

## 五、方案说明

本方案基于Penman-Monteith公式计算参考作物需水量，结合`{{ location }}`实时气象数据和土壤特性，制定了分生育期的灌溉制度。设备选型遵循经济性和可靠性原则，所有数据来自Open-Meteo实时气象API。
"""