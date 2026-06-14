from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EquipmentAgent:
    def __init__(self, db: Session, knowledge_forest: Optional[Any] = None):
        self.db = db
        self.knowledge_forest = knowledge_forest

    async def select(self, irrigation_plan: Dict[str, Any], budget: float) -> Dict[str, Any]:
        irrigation_method = irrigation_plan["project_info"].get("irrigation_method", "喷灌")
        area = irrigation_plan["project_info"].get("area", 1000)
        total_water = irrigation_plan["project_info"].get("total_water_requirement_m3", 10000)
        crop_type = irrigation_plan["project_info"].get("crop_type", "")
        location = irrigation_plan["project_info"].get("location", "")

        kf_equip_knowledge = await self._query_equipment_knowledge(irrigation_method, crop_type, location)

        equipment_list = self._get_equipment_list(irrigation_method, area, total_water)

        equipment_list = self._enrich_from_knowledge(equipment_list, kf_equip_knowledge)

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

    async def _query_equipment_knowledge(
        self, method: str, crop_type: str, location: str
    ) -> List[Dict[str, Any]]:
        if self.knowledge_forest is None:
            return []

        try:
            query = f"{method} {crop_type} {location} 设备选型 配置方案"
            results = await self.knowledge_forest.query(query, k=3)
            logger.info(f"[设备选型] 知识森林检索 '{query}' -> {len(results)}条")
            return results
        except Exception as e:
            logger.warning(f"[设备选型] 知识森林查询失败: {e}")
            return []

    def _enrich_from_knowledge(
        self, equipment_list: List[Dict], knowledge: List[Dict]
    ) -> List[Dict]:
        if not knowledge:
            return equipment_list

        kf_text = " ".join(k.get("content", "") for k in knowledge)

        for item in equipment_list:
            if item["name"] in kf_text:
                item["knowledge_referenced"] = True

        has_iot = any(kw in kf_text for kw in ["物联网", "IoT", "传感器网络", "智能监测"])
        has_automation = any(kw in kf_text for kw in ["自动控制", "智能控制", "远程", "Docker", "Kubernetes"])

        if has_iot or has_automation:
            already_has_controller = any(e["name"] == "控制器" for e in equipment_list)
            if already_has_controller:
                logger.info("[设备选型] 知识森林建议增强智能控制能力")
                for item in equipment_list:
                    if item["name"] == "控制器":
                        item["note"] = "知识森林建议：配置智能控制与远程管理模块"

        return equipment_list

    def _get_equipment_list(self, method: str, area: float, total_water: float) -> List[Dict[str, Any]]:
        base_equipment = []

        if method == "滴灌":
            base_equipment = [
                {"name": "滴灌带", "model": "DG-16-30", "quantity": int(area * 1.2), "price": 0.8, "unit": "米"},
                {"name": "滴灌管", "model": "DG-20-50", "quantity": int(area * 0.1), "price": 5.0, "unit": "米"},
                {"name": "电磁阀", "model": "SV-24V", "quantity": int(area / 50) + 1, "price": 120.0, "unit": "个"},
                {"name": "过滤器", "model": "F-200", "quantity": 2, "price": 800.0, "unit": "台"},
                {"name": "水肥一体机", "model": "HF-500", "quantity": 1, "price": 15000.0, "unit": "台"},
                {"name": "传感器", "model": "SM-100", "quantity": int(area / 100) + 1, "price": 350.0, "unit": "个"},
                {"name": "控制器", "model": "CT-200", "quantity": 1, "price": 3000.0, "unit": "台"}
            ]
        elif method == "喷灌":
            base_equipment = [
                {"name": "喷头", "model": "SP-360", "quantity": int(area / 0.15), "price": 85.0, "unit": "个"},
                {"name": "喷灌管", "model": "PG-50", "quantity": int(area * 0.15), "price": 8.0, "unit": "米"},
                {"name": "电磁阀", "model": "SV-24V", "quantity": int(area / 100) + 1, "price": 120.0, "unit": "个"},
                {"name": "过滤器", "model": "F-300", "quantity": 2, "price": 1200.0, "unit": "台"},
                {"name": "水泵", "model": "P-5.5KW", "quantity": 2, "price": 2500.0, "unit": "台"},
                {"name": "传感器", "model": "SM-100", "quantity": int(area / 100) + 1, "price": 350.0, "unit": "个"},
                {"name": "控制器", "model": "CT-300", "quantity": 1, "price": 4500.0, "unit": "台"}
            ]
        else:
            base_equipment = [
                {"name": "渠道衬砌", "model": "CL-10", "quantity": int(area * 0.05), "price": 120.0, "unit": "米"},
                {"name": "闸门", "model": "G-100", "quantity": 5, "price": 800.0, "unit": "个"},
                {"name": "流量计", "model": "FM-50", "quantity": 3, "price": 1500.0, "unit": "台"},
                {"name": "水泵", "model": "P-11KW", "quantity": 2, "price": 5000.0, "unit": "台"}
            ]

        return base_equipment

    def _optimize_for_budget(self, equipment_list: List[Dict[str, Any]], budget: float) -> List[Dict[str, Any]]:
        total_cost = sum(item["price"] * item["quantity"] for item in equipment_list)
        ratio = budget / total_cost

        if ratio < 0.5:
            non_critical_items = ["传感器", "水肥一体机", "控制器"]
            equipment_list = [item for item in equipment_list if item["name"] not in non_critical_items]
            total_cost = sum(item["price"] * item["quantity"] for item in equipment_list)
            ratio = budget / total_cost

        optimized_list = []
        for item in equipment_list:
            if item["name"] in ["过滤器", "水泵"]:
                optimized_list.append(item)
            else:
                new_quantity = max(1, int(item["quantity"] * ratio))
                optimized_list.append({**item, "quantity": new_quantity})

        return optimized_list