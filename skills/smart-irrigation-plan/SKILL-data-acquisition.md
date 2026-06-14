---
name: data-acquisition
description: >
  气象与土壤数据采集技能。调用Open-Meteo API获取指定地点的实时气象数据
  （气温、湿度、风速、ET0）和土壤数据（土壤温度、土壤湿度、推断土壤类型），
  支持坐标解析。代码位于 backend/data_acquisition/main.py，核心类：DataAcquisition。
---

# 数据采集技能 — Open-Meteo API封装与坐标解析

## 何时使用

当 `RequirementAgent` 需要获取项目地点的气象和土壤数据时，通过 `DataAcquisition` 实例调用。也支持用户直接通过 `/acquire-weather` 和 `/acquire-soil` 端点查询。代码位置：`backend/data_acquisition/main.py`。

## 工作流程

### 第1步：坐标解析

调用 `resolve_coords(location)`（`main.py:166`），从 `CHINA_COORDS` 字典中匹配坐标：

```python
CHINA_COORDS = {
    # 省级坐标（17个）
    "河北": (38.0423, 114.5025), "河南": (34.7466, 113.6253), "山东": (36.6777, 116.9850),
    "江苏": (32.0603, 118.7969), "浙江": (30.2741, 120.1552), "安徽": (31.8654, 117.2272),
    # ... 共17个省
    # 城市坐标（70+个）
    "北京": (39.9042, 116.4074), "郑州市": (34.7466, 113.6253), "中牟县": (34.7195, 113.9769),
    "济南市": (36.6777, 116.9850), "南京市": (32.0603, 118.7969), ...
    # 区县级坐标（60+个）
    "朝阳区": (39.9219, 116.4435), "海淀区": (39.9561, 116.3103), ...
    # 总计150+个地点坐标
}

def resolve_coords(self, location: str) -> tuple:
    # 1. 精确匹配
    if location in CHINA_COORDS:
        return CHINA_COORDS[location]
    
    # 2. 模糊匹配（取最长匹配）
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
    
    # 3. 默认北京
    logger.warning(f"[坐标解析] 未找到 '{location}'，使用默认坐标")
    return (39.9042, 116.4074)
```

### 第2步：获取气象数据

调用 `get_weather_data(location)`（`main.py:185`）：

```python
async def get_weather_data(self, location: str) -> Dict[str, Any]:
    lat, lon = self.resolve_coords(location)
    
    try:
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
        return result
    except Exception as e:
        return {
            "location": location, "latitude": lat, "longitude": lon,
            "temperature": 25.0, "humidity": 60.0, "wind_speed": 2.0,
            "et0": 3.5, "source": f"API Error - Default",
        }
```

**API参数说明**：
- `current`: 获取实时数据（非预报数据）
- `temperature_2m`: 2米气温（℃）
- `relative_humidity_2m`: 2米相对湿度（%）
- `wind_speed_10m`: 10米风速（m/s）
- `et0_fao_evapotranspiration`: FAO参考作物蒸散发量（mm/d）

### 第3步：获取土壤数据

调用 `get_soil_data(location)`（`main.py:225`）：

```python
async def get_soil_data(self, location: str) -> Dict[str, Any]:
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
        
        # 24小时平均土壤温度（0cm深度）
        avg_temp = round(sum(st0) / len(st0), 1) if st0 else 15
        
        # 三层土壤湿度加权平均
        avg_moisture = round(
            (sum(sm01) / len(sm01) + sum(sm13) / len(sm13) + sum(sm39) / len(sm39)) / 3, 3
        ) if sm01 else 0.3
        
        # 根据湿度推断土壤类型
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
        return result
    except Exception as e:
        return {
            "location": location, "latitude": lat, "longitude": lon,
            "soil_type": "壤土", "soil_temperature": 15.0,
            "field_capacity": 0.32, "wilting_point": 0.12,
            "available_water": 0.20, "source": f"API Error - Default",
        }
```

### 土壤类型推断

调用 `_classify_from_moisture(moisture)`（`main.py:278`）：

```python
def _classify_from_moisture(self, moisture: float) -> str:
    if moisture > 0.4:
        return "粘土"
    elif moisture < 0.2:
        return "砂土"
    elif moisture > 0.3:
        return "壤土"
    return "粉壤土"
```

### 土壤水分参数查询

调用 `_water_params(soil_type)`（`main.py:287`）：

```python
def _water_params(self, soil_type: str) -> tuple:
    return {
        "粘土": (0.42, 0.18, 0.24),    # (field_capacity, wilting_point, available_water)
        "壤土": (0.32, 0.12, 0.20),
        "砂土": (0.18, 0.06, 0.12),
        "粉壤土": (0.28, 0.10, 0.18),
    }.get(soil_type, (0.32, 0.12, 0.20))  # 默认壤土参数
```

## 决策标准

| 场景 | 决策 |
|-----|------|
| 精确匹配CHINA_COORDS | 直接返回坐标 |
| 模糊匹配（location包含字典key） | 取最长匹配的坐标 |
| 无匹配 | 默认北京坐标(39.9042, 116.4074) |
| Open-Meteo API调用失败 | 返回默认值（temperature=25, humidity=60, et0=3.5） |
| 土壤湿度异常（无数据） | 默认0.3 |
| moisture > 0.4 | 推断为"粘土" |
| moisture < 0.2 | 推断为"砂土" |
| 0.2 ≤ moisture ≤ 0.3 | 推断为"粉壤土" |
| 0.3 < moisture ≤ 0.4 | 推断为"壤土" |

## 工具使用

| 工具 | 调用方式 | 输入 | 输出 |
|-----|---------|------|------|
| Open-Meteo API (气象) | HTTP GET (requests) | lat, lon, current参数 | JSON响应（temperature/humidity/wind_speed/et0） |
| Open-Meteo API (土壤) | HTTP GET (requests) | lat, lon, hourly参数 | JSON响应（soil_temperature/soil_moisture） |
| `CHINA_COORDS`字典 | 本地查找 | location字符串 | (lat, lon)元组，覆盖150+城市 |
| `requests.get()` | HTTP请求 | url, params, timeout=30 | Response对象 |

## 质量检查

在返回数据前确认：
- [ ] temperature 在 -20~50°C 范围内（由API保证）
- [ ] humidity 在 0~100% 范围内
- [ ] wind_speed ≥ 0
- [ ] et0 > 0（API返回值，默认3.5）
- [ ] soil_type 为有效值之一（粘土/壤土/砂土/粉壤土）
- [ ] field_capacity > wilting_point（土壤水分物理约束）
- [ ] available_water = field_capacity - wilting_point
- [ ] source 标注清晰（"Open-Meteo API (实时)" 或 "Open-Meteo API" 或 "API Error - Default"）

## 输出规范

```json
// 气象数据（get_weather_data返回）
{
    "location": "河南中牟县",
    "latitude": 34.7195,
    "longitude": 113.9769,
    "temperature": 25.5,
    "humidity": 65.0,
    "wind_speed": 2.3,
    "et0": 4.2,
    "time": "2024-01-15T14:00",
    "source": "Open-Meteo API (实时)"
}

// 土壤数据（get_soil_data返回）
{
    "location": "河南中牟县",
    "latitude": 34.7195,
    "longitude": 113.9769,
    "soil_type": "壤土",
    "soil_temperature": 15.3,
    "surface_moisture": 0.28,
    "field_capacity": 0.32,
    "wilting_point": 0.12,
    "available_water": 0.20,
    "source": "Open-Meteo API"
}
```

> **代码位置**: `backend/data_acquisition/main.py`
> **核心类**: `DataAcquisition`
> **核心方法**: `resolve_coords()`(line 166), `get_weather_data()`(line 185), `get_soil_data()`(line 225), `_classify_from_moisture()`(line 278), `_water_params()`(line 287)
> **坐标字典**: `CHINA_COORDS`(line 9), 覆盖150+城市/区县