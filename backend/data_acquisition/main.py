import requests
import os
import logging
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHINA_COORDS = {
    "北京": (39.9042, 116.4074),
    "朝阳区": (39.9219, 116.4435),
    "海淀区": (39.9561, 116.3103),
    "丰台区": (39.8585, 116.2870),
    "通州区": (39.9023, 116.6572),
    "大兴区": (39.7268, 116.3386),
    "顺义区": (40.1302, 116.6544),
    "昌平区": (40.2207, 116.2312),
    "房山区": (39.7572, 116.1394),
    "石家庄市": (38.0423, 114.5025),
    "正定县": (38.1464, 114.5710),
    "辛集市": (37.9432, 115.2181),
    "晋州市": (38.0316, 115.0442),
    "藁城区": (38.0217, 114.8478),
    "鹿泉区": (38.0859, 114.3138),
    "唐山市": (39.6305, 118.1802),
    "邯郸市": (36.6256, 114.5390),
    "保定市": (38.8739, 115.4646),
    "定州市": (38.5162, 115.0001),
    "涿州市": (39.4856, 115.9742),
    "济南市": (36.6777, 116.9850),
    "历下区": (36.6664, 117.0765),
    "市中区": (36.6512, 116.9978),
    "章丘区": (36.6813, 117.5362),
    "青岛市": (36.0671, 120.3826),
    "黄岛区": (35.9607, 120.1981),
    "即墨区": (36.3893, 120.4472),
    "烟台市": (37.4635, 121.4479),
    "潍坊市": (36.7068, 119.1618),
    "寿光市": (36.8555, 118.7910),
    "郑州市": (34.7466, 113.6253),
    "中牟县": (34.7195, 113.9769),
    "新郑市": (34.3960, 113.7408),
    "开封市": (34.7977, 114.3073),
    "洛阳市": (34.6181, 112.4540),
    "新乡市": (35.3037, 113.9268),
    "安阳市": (36.0977, 114.3931),
    "南京市": (32.0603, 118.7969),
    "江宁区": (31.9527, 118.8400),
    "六合区": (32.3222, 118.8222),
    "苏州市": (31.2990, 120.5853),
    "昆山市": (31.3856, 120.9810),
    "无锡市": (31.4912, 120.3119),
    "常州市": (31.8107, 119.9740),
    "徐州市": (34.2058, 117.2841),
    "合肥市": (31.8654, 117.2272),
    "肥西县": (31.7066, 117.1694),
    "杭州市": (30.2741, 120.1552),
    "萧山区": (30.1853, 120.2645),
    "余杭区": (30.4185, 120.2998),
    "宁波市": (29.8683, 121.5440),
    "温州市": (27.9938, 120.6994),
    "武汉市": (30.5928, 114.3055),
    "黄陂区": (30.8823, 114.3758),
    "荆州市": (30.3348, 112.2407),
    "长沙市": (28.2280, 112.9388),
    "浏阳市": (28.1638, 113.6430),
    "宁乡市": (28.2781, 112.5520),
    "广州市": (23.1291, 113.2644),
    "番禺区": (22.9370, 113.3537),
    "增城区": (23.2609, 113.8109),
    "深圳市": (22.5431, 114.0579),
    "龙岗区": (22.7208, 114.2469),
    "宝安区": (22.5552, 113.8830),
    "佛山市": (23.0218, 113.1219),
    "东莞市": (23.0208, 113.7518),
    "成都市": (30.5728, 104.0668),
    "双流区": (30.5745, 103.9239),
    "郫都区": (30.7959, 103.9026),
    "西安市": (34.2772, 108.9481),
    "长安区": (34.1589, 108.9418),
    "临潼区": (34.3673, 109.2143),
    "咸阳市": (34.3291, 108.7093),
    "杨凌区": (34.2830, 108.0808),
    "兰州市": (36.0611, 103.8343),
    "银川市": (38.4789, 106.2783),
    "呼和浩特市": (40.8173, 111.6772),
    "太原市": (37.8706, 112.5492),
    "沈阳市": (41.8045, 123.4328),
    "长春市": (43.8868, 125.3245),
    "哈尔滨市": (45.8038, 126.5350),
    "上海市": (31.2304, 121.4737),
    "浦东新区": (31.2213, 121.5447),
    "崇明区": (31.6129, 121.3973),
    "天津市": (39.1422, 117.2079),
    "滨海新区": (39.0032, 117.7107),
    "重庆市": (29.4316, 106.9123),
    "贵阳市": (26.5789, 106.7132),
    "昆明市": (25.0389, 102.7183),
    "南昌市": (28.6843, 115.8922),
    "福州市": (26.0799, 119.3062),
    "南宁市": (22.8157, 108.3200),
    "海口市": (20.0319, 110.3312),
    "拉萨市": (29.6433, 91.1355),
    "乌鲁木齐市": (43.8261, 87.6168),
    "西宁市": (36.6231, 101.7798),
    "河北": (38.0423, 114.5025),
    "河南": (34.7466, 113.6253),
    "山东": (36.6777, 116.9850),
    "江苏": (32.0603, 118.7969),
    "浙江": (30.2741, 120.1552),
    "安徽": (31.8654, 117.2272),
    "福建": (26.0799, 119.3062),
    "江西": (28.6843, 115.8922),
    "湖北": (30.5928, 114.3055),
    "湖南": (28.2280, 112.9388),
    "广东": (23.1291, 113.2644),
    "广西": (22.8157, 108.3200),
    "海南": (20.0319, 110.3312),
    "四川": (30.5728, 104.0668),
    "云南": (25.0389, 102.7183),
    "贵州": (26.5789, 106.7132),
    "陕西": (34.2772, 108.9481),
    "甘肃": (36.0611, 103.8343),
    "青海": (36.6231, 101.7798),
    "宁夏": (38.4789, 106.2783),
    "新疆": (43.8261, 87.6168),
    "西藏": (29.6433, 91.1355),
    "内蒙古": (40.8173, 111.6772),
    "山西": (37.8706, 112.5492),
    "辽宁": (41.8045, 123.4328),
    "吉林": (43.8868, 125.3245),
    "黑龙江": (45.8038, 126.5350),
    "衢州": (28.9352, 118.8746),
    "衢州市": (28.9352, 118.8746),
    "龙游": (29.0268, 119.1768),
    "龙游县": (29.0268, 119.1768),
    "江山": (28.7377, 118.6329),
    "江山市区": (28.7377, 118.6329),
    "柯城区": (28.9416, 118.8466),
    "衢江区": (28.9806, 118.9805),
    "湖州": (30.8930, 120.0885),
    "湖州市": (30.8930, 120.0885),
    "嘉兴": (30.7626, 120.7543),
    "嘉兴市": (30.7626, 120.7543),
    "金华": (29.0781, 119.6544),
    "金华市": (29.0781, 119.6544),
    "义乌": (29.3058, 120.0742),
    "义乌市": (29.3058, 120.0742),
    "台州": (28.6556, 121.4234),
    "台州市": (28.6556, 121.4234),
    "绍兴": (30.0297, 120.5862),
    "绍兴市": (30.0297, 120.5862),
    "舟山": (30.0156, 122.1150),
    "舟山市": (30.0156, 122.1150),
    "丽水": (28.4516, 119.9157),
    "丽水市": (28.4516, 119.9157),
}


class DataAcquisition:
    def __init__(self):
        self.openmeteo_url = os.environ.get(
            "OPENMETEO_API_URL", "https://api.open-meteo.com/v1/forecast"
        )

    def resolve_coords(self, location: str) -> tuple:
        if location in CHINA_COORDS:
            return CHINA_COORDS[location]

        best_match = None
        best_len = 0
        for name, coords in CHINA_COORDS.items():
            if name in location and len(name) >= best_len and len(name) > 0:
                best_len = len(name)
                best_match = name, coords

        if best_match:
            name, coords = best_match
            logger.info(f"[坐标解析] '{location}' -> '{name}' ({coords[0]}, {coords[1]})")
            return coords

        logger.warning(f"[坐标解析] 未找到 '{location}'，使用默认坐标")
        return (39.9042, 116.4074)

    async def get_weather_data(self, location: str) -> Dict[str, Any]:
        logger.info(f"[气象采集] 获取 {location} 的气象数据")
        lat, lon = self.resolve_coords(location)
        logger.info(f"[气象采集] 坐标: ({lat}, {lon})")

        try:
            # 使用 current 参数获取实时气象数据（非预报）
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,et0_fao_evapotranspiration",
                "timezone": "Asia/Shanghai",
            }
            resp = requests.get(self.openmeteo_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            current = data.get("current", {})

            result = {
                "location": location,
                "latitude": lat,
                "longitude": lon,
                "temperature": round(current.get("temperature_2m", 25), 1),
                "humidity": round(current.get("relative_humidity_2m", 60), 1),
                "wind_speed": round(current.get("wind_speed_10m", 2), 1),
                "et0": round(current.get("et0_fao_evapotranspiration", 3.5), 2),
                "time": current.get("time", "N/A"),
                "source": "Open-Meteo API (实时)",
            }
            logger.info(f"[气象采集] 实时数据成功: {result['temperature']}°C, ET0={result['et0']}mm/d")
            return result
        except Exception as e:
            logger.error(f"[气象采集] 失败: {e}")
            return {
                "location": location, "latitude": lat, "longitude": lon,
                "temperature": 25.0, "humidity": 60.0, "wind_speed": 2.0,
                "et0": 3.5, "source": f"API Error - Default",
            }

    async def get_soil_data(self, location: str) -> Dict[str, Any]:
        logger.info(f"[土壤采集] 获取 {location} 的土壤数据")
        lat, lon = self.resolve_coords(location)

        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "soil_temperature_0cm,soil_temperature_18cm,soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm",
                "timezone": "Asia/Shanghai",
                "forecast_days": 1,
            }
            resp = requests.get(self.openmeteo_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            hourly = data.get("hourly", {})
            st0 = hourly.get("soil_temperature_0cm", [15])
            sm01 = hourly.get("soil_moisture_0_to_1cm", [0.3])
            sm13 = hourly.get("soil_moisture_1_to_3cm", [0.3])
            sm39 = hourly.get("soil_moisture_3_to_9cm", [0.3])

            avg_temp = round(sum(st0) / len(st0), 1) if st0 else 15
            avg_moisture = round(
                (sum(sm01) / len(sm01) + sum(sm13) / len(sm13) + sum(sm39) / len(sm39)) / 3, 3
            ) if sm01 else 0.3

            soil_type = self._classify_from_moisture(avg_moisture)
            fc, wp, aw = self._water_params(soil_type)

            result = {
                "location": location,
                "latitude": lat,
                "longitude": lon,
                "soil_type": soil_type,
                "soil_temperature": avg_temp,
                "surface_moisture": avg_moisture,
                "field_capacity": fc,
                "wilting_point": wp,
                "available_water": aw,
                "source": "Open-Meteo API",
            }
            logger.info(f"[土壤采集] 成功: {soil_type}, moisture={avg_moisture}")
            return result
        except Exception as e:
            logger.error(f"[土壤采集] 失败: {e}")
            return {
                "location": location, "latitude": lat, "longitude": lon,
                "soil_type": "壤土", "soil_temperature": 15.0,
                "field_capacity": 0.32, "wilting_point": 0.12,
                "available_water": 0.20, "source": f"API Error - Default",
            }

    def _classify_from_moisture(self, moisture: float) -> str:
        if moisture > 0.4:
            return "粘土"
        elif moisture < 0.2:
            return "砂土"
        elif moisture > 0.3:
            return "壤土"
        return "粉壤土"

    def _water_params(self, soil_type: str) -> tuple:
        return {
            "粘土": (0.42, 0.18, 0.24),
            "壤土": (0.32, 0.12, 0.20),
            "砂土": (0.18, 0.06, 0.12),
            "粉壤土": (0.28, 0.10, 0.18),
        }.get(soil_type, (0.32, 0.12, 0.20))