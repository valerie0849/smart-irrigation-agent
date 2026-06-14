from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
import numpy as np
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IrrigationDesignAgent:
    def __init__(self, db: Session, knowledge_forest: Optional[Any] = None):
        self.db = db
        self.knowledge_forest = knowledge_forest

    async def design(self, project_profile: Dict[str, Any]) -> Dict[str, Any]:
        weather_data = project_profile.get("weather_data", {})
        crop_params = project_profile.get("crop_parameters", {})
        soil_params = project_profile.get("soil_parameters", {})
        area = project_profile.get("area", 1000)
        location = project_profile.get("location", "")
        crop_type = project_profile.get("crop_type", "")

        kf_design_knowledge = await self._query_design_knowledge(location, crop_type)

        et0 = self._calculate_et0(weather_data)

        adjusted_kc = self._adjust_kc_from_knowledge(crop_params, kf_design_knowledge)

        irrigation_schedule = self._generate_irrigation_schedule(et0, adjusted_kc, soil_params)

        total_water_requirement = sum(period["water_requirement_mm"] for period in irrigation_schedule)

        irrigation_method = self._select_irrigation_method(soil_params, kf_design_knowledge)

        design_result = {
            "project_info": {
                "location": location,
                "crop_type": crop_type,
                "area": area,
                "total_water_requirement_m3": round(total_water_requirement * area * 666.67 / 1000, 2),
                "irrigation_method": irrigation_method,
                "knowledge_informed": bool(kf_design_knowledge),
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

        return design_result

    async def _query_design_knowledge(self, location: str, crop_type: str) -> List[Dict[str, Any]]:
        if self.knowledge_forest is None:
            return []

        try:
            query = f"{crop_type} {location} 灌溉制度 灌水定额 Kc系数 设计方案"
            results = await self.knowledge_forest.query(query, k=5)
            logger.info(f"[灌溉设计] 知识森林检索 '{query}' -> {len(results)}条")
            return results
        except Exception as e:
            logger.warning(f"[灌溉设计] 知识森林查询失败: {e}")
            return []

    def _adjust_kc_from_knowledge(
        self, crop_params: Dict[str, Any], knowledge: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        adjusted = dict(crop_params)
        if not knowledge:
            return adjusted

        kc_keywords = ["Kc", "作物系数", "kc"]
        found_kc_hints = 0
        for item in knowledge:
            content = item.get("content", "")
            for kw in kc_keywords:
                if kw in content:
                    found_kc_hints += 1
                    break

        if found_kc_hints >= 2:
            logger.info(f"[灌溉设计] 知识森林中找到{found_kc_hints}条Kc相关记录，保留数据库Kc但标注知识来源")
            adjusted["knowledge_validated"] = True

        return adjusted

    def _select_irrigation_method(
        self, soil_params: Dict[str, Any], knowledge: List[Dict[str, Any]] = None
    ) -> str:
        soil_type = soil_params.get("soil_type", "壤土")

        method_from_knowledge = None
        if knowledge:
            drip_count = sum(1 for item in knowledge if "滴灌" in item.get("content", ""))
            spray_count = sum(1 for item in knowledge if "喷灌" in item.get("content", ""))
            if drip_count > spray_count and drip_count > 0:
                method_from_knowledge = "滴灌"
            elif spray_count > drip_count and spray_count > 0:
                method_from_knowledge = "喷灌"

        if soil_type == "砂土":
            return method_from_knowledge if method_from_knowledge else "滴灌"
        elif soil_type == "粘土":
            return method_from_knowledge if method_from_knowledge else "沟灌"
        else:
            return method_from_knowledge if method_from_knowledge else "喷灌"
    
    def _calculate_et0(self, weather_data: Dict[str, Any]) -> float:
        temperature = weather_data.get("temperature", 25)
        humidity = weather_data.get("humidity", 60)
        wind_speed = weather_data.get("wind_speed", 2)
        sunshine_hours = weather_data.get("sunshine_hours", 8)
        
        try:
            delta = 4098 * (0.6108 * np.exp((17.27 * temperature) / (temperature + 237.3))) / ((temperature + 237.3) ** 2)
            
            gamma = 0.00163 * 101.3 / (2.45 * (temperature + 273.15))
            
            ra = (24 * 60 / np.pi) * 4.92 * (sunshine_hours / 12)
            
            rs = 0.5 * ra
            
            rso = 0.75 * ra
            
            rn = 0.77 * rs
            
            g = 0
            
            et0 = (0.408 * delta * (rn - g) + gamma * (900 / (temperature + 273)) * wind_speed * (0.6108 * np.exp((17.27 * temperature) / (temperature + 237.3)) - humidity / 100)) / (delta + gamma * (1 + 0.34 * wind_speed))
            
            return round(max(et0, 1.0), 2)
        except:
            return 3.5
    
    def _generate_irrigation_schedule(self, et0: float, crop_params: Dict[str, Any], soil_params: Dict[str, Any]) -> List[Dict[str, Any]]:
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
        
        available_water = (field_capacity - wilting_point) * root_depth * 1000
        
        for stage in stages:
            etc = et0 * stage["kc"]
            water_requirement = etc * stage["days"]
            irrigation_amount = available_water * 0.6
            irrigation_frequency = int(np.ceil(available_water * 0.6 / etc))
            
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