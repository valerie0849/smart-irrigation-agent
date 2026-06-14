from sqlalchemy.orm import Session
from backend.data_acquisition import DataAcquisition, CHINA_COORDS
from backend.src.models import CropParameter, SoilParameter
from typing import Dict, Any, Tuple
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


REGION_CROP_MAP = {
    "黑龙江": "水稻",
    "吉林": "玉米",
    "辽宁": "玉米",
    "北京": "小麦",
    "天津": "小麦",
    "河北": "小麦",
    "山西": "小麦",
    "山东": "小麦",
    "河南": "小麦",
    "陕西": "小麦",
    "甘肃": "小麦",
    "宁夏": "小麦",
    "青海": "小麦",
    "新疆": "棉花",
    "内蒙古": "玉米",
    "江苏": "水稻",
    "上海": "水稻",
    "浙江": "水稻",
    "安徽": "水稻",
    "江西": "水稻",
    "湖北": "水稻",
    "湖南": "水稻",
    "福建": "水稻",
    "广东": "水稻",
    "广西": "水稻",
    "海南": "水稻",
    "四川": "水稻",
    "重庆": "水稻",
    "贵州": "水稻",
    "云南": "水稻",
    "西藏": "小麦",
}


def _infer_crop_by_location(location: str) -> Tuple[str, str]:
    for region, crop in REGION_CROP_MAP.items():
        if region in location:
            return crop, region
    return "", ""


class RequirementAgent:
    def __init__(self, db: Session):
        self.db = db
        self.data_acquisition = DataAcquisition()

    @staticmethod
    def _raw_extract_entities(text: str) -> Dict[str, Any]:
        entities = {}
        area_units = ["亩", "公顷", "平方米"]
        for unit in area_units:
            if unit in text:
                match = re.search(r"(\d+(?:\.\d+)?)%s" % unit, text)
                if match:
                    area_value = float(match.group(1))
                    if unit == "公顷":
                        area_value *= 15
                    elif unit == "平方米":
                        area_value /= 666.67
                    entities["area"] = round(area_value, 2)

        budget_units = ["万", "元"]
        for unit in budget_units:
            if unit in text:
                match = re.search(r"(\d+(?:\.\d+)?)%s" % unit, text)
                if match:
                    budget_value = float(match.group(1))
                    if unit == "万":
                        budget_value *= 10000
                    entities["budget"] = int(budget_value)

        crop_keywords = ["小麦", "水稻", "玉米", "棉花", "蔬菜", "果树", "大豆", "花生", "油菜"]
        for crop in crop_keywords:
            if crop in text:
                entities["crop_type"] = crop
                break

        best_loc = ""
        best_len = 0
        for loc in CHINA_COORDS.keys():
            if loc in text and len(loc) >= best_len and len(loc) > 0:
                best_loc = loc
                best_len = len(loc)
        if best_loc:
            entities["location"] = best_loc

        return entities

    async def analyze(self, user_input: str, accumulated: dict = None) -> Dict[str, Any]:
        entities = self._extract_entities(user_input)

        if accumulated:
            for k, v in accumulated.items():
                if v and not entities.get(k):
                    entities[k] = v

        location = entities.get("location", "北京")
        area = entities.get("area", 1000)
        budget = entities.get("budget", 1000000)

        crop_type = entities.get("crop_type")
        crop_inferred = False
        if not crop_type:
            inferred_crop, matched_region = _infer_crop_by_location(location)
            if inferred_crop:
                crop_type = inferred_crop
                crop_inferred = True
                logger.info(f"[需求] 根据地区'{location}'(匹配{matched_region})推断作物: {crop_type}")
            else:
                crop_type = "小麦"
                logger.warning(f"[需求] 无法推断'{location}'的作物，回退为小麦")
        
        weather_data = await self.data_acquisition.get_weather_data(location)
        soil_data = await self.data_acquisition.get_soil_data(location)
        
        crop_params = self._get_crop_parameters(crop_type)
        soil_params = self._get_soil_parameters(soil_data.get("soil_type", "壤土"))
        
        project_profile = {
            "location": location,
            "crop_type": crop_type,
            "area": area,
            "budget": budget,
            "weather_data": weather_data,
            "soil_data": soil_data,
            "crop_parameters": crop_params,
            "soil_parameters": soil_params,
            "original_input": user_input,
            "crop_inferred": crop_inferred,
        }
        
        return project_profile
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        entities = {}
        
        area_units = ["亩", "公顷", "平方米"]
        for unit in area_units:
            if unit in text:
                match = re.search(r"(\d+(?:\.\d+)?)%s" % unit, text)
                if match:
                    area_value = float(match.group(1))
                    if unit == "公顷":
                        area_value *= 15
                    elif unit == "平方米":
                        area_value /= 666.67
                    entities["area"] = round(area_value, 2)
        
        budget_units = ["万", "元"]
        for unit in budget_units:
            if unit in text:
                match = re.search(r"(\d+(?:\.\d+)?)%s" % unit, text)
                if match:
                    budget_value = float(match.group(1))
                    if unit == "万":
                        budget_value *= 10000
                    entities["budget"] = int(budget_value)
        
        crop_keywords = ["小麦", "水稻", "玉米", "棉花", "蔬菜", "果树", "大豆", "花生", "油菜"]
        for crop in crop_keywords:
            if crop in text:
                entities["crop_type"] = crop
                break
        
        best_loc = ""
        best_len = 0
        for loc in CHINA_COORDS.keys():
            if loc in text and len(loc) >= best_len and len(loc) > 0:
                best_loc = loc
                best_len = len(loc)
        if best_loc:
            entities["location"] = best_loc
        
        return entities
    
    def _get_crop_parameters(self, crop_name: str) -> Dict[str, Any]:
        crop = self.db.query(CropParameter).filter(CropParameter.crop_name == crop_name).first()
        if crop:
            return {
                "crop_name": crop.crop_name,
                "stage_1_kc": crop.stage_1_kc,
                "stage_2_kc": crop.stage_2_kc,
                "stage_3_kc": crop.stage_3_kc,
                "stage_4_kc": crop.stage_4_kc,
                "root_depth": crop.root_depth,
                "stage_1_days": crop.stage_1_days,
                "stage_2_days": crop.stage_2_days,
                "stage_3_days": crop.stage_3_days,
                "stage_4_days": crop.stage_4_days
            }
        
        default_params = {
            "小麦": {"stage_1_kc": 0.3, "stage_2_kc": 0.5, "stage_3_kc": 1.15, "stage_4_kc": 0.4, "root_depth": 0.8, "stage_1_days": 30, "stage_2_days": 45, "stage_3_days": 60, "stage_4_days": 30},
            "水稻": {"stage_1_kc": 1.1, "stage_2_kc": 1.2, "stage_3_kc": 1.15, "stage_4_kc": 0.9, "root_depth": 0.6, "stage_1_days": 25, "stage_2_days": 50, "stage_3_days": 45, "stage_4_days": 20},
            "玉米": {"stage_1_kc": 0.3, "stage_2_kc": 0.5, "stage_3_kc": 1.1, "stage_4_kc": 0.5, "root_depth": 1.0, "stage_1_days": 25, "stage_2_days": 35, "stage_3_days": 45, "stage_4_days": 20}
        }
        
        return default_params.get(crop_name, default_params["小麦"])
    
    def _get_soil_parameters(self, soil_type: str) -> Dict[str, Any]:
        soil = self.db.query(SoilParameter).filter(SoilParameter.soil_type == soil_type).first()
        if soil:
            return {
                "soil_type": soil.soil_type,
                "field_capacity": soil.field_capacity,
                "wilting_point": soil.wilting_point,
                "available_water": soil.available_water
            }
        
        default_soil = {
            "壤土": {"field_capacity": 0.32, "wilting_point": 0.12, "available_water": 0.20},
            "砂土": {"field_capacity": 0.18, "wilting_point": 0.06, "available_water": 0.12},
            "粘土": {"field_capacity": 0.42, "wilting_point": 0.18, "available_water": 0.24}
        }
        
        return default_soil.get(soil_type, default_soil["壤土"])