---
name: requirement-analysis
description: >
  灌溉项目需求分析技能。从用户文本中提取项目实体（地点、作物、面积、预算），
  自动推断缺失作物类型（使用REGION_CROP_MAP），调用Open-Meteo API获取实时气象和土壤数据，
  从数据库查询作物参数和土壤参数，输出完整的项目概况。
  代码位于 backend/agents/requirement.py，核心类：RequirementAgent，核心方法：analyze()。
---

# 需求分析技能 — 实体提取与数据补全

## 何时使用

当调度技能已完成意图分类（`generate`），且用户输入信息完整（无缺失信息），调用 `RequirementAgent.analyze(ui, accumulated)` 构建项目概况。代码位置：`backend/agents/requirement.py`。

## 工作流程

### 第1步：实体提取

调用 `_extract_entities(text)` 方法（`requirement.py:146`），从文本中提取四个核心实体：

```python
def _extract_entities(self, text: str) -> Dict[str, Any]:
    entities = {}
    
    # 1. 面积提取：支持亩、公顷(×15)、平方米(÷666.67)
    area_units = ["亩", "公顷", "平方米"]
    for unit in area_units:
        if unit in text:
            match = re.search(r"(\d+(?:\.\d+)?)%s" % unit, text)
            if match:
                area_value = float(match.group(1))
                if unit == "公顷": area_value *= 15
                elif unit == "平方米": area_value /= 666.67
                entities["area"] = round(area_value, 2)
    
    # 2. 预算提取：支持万(×10000)、元
    budget_units = ["万", "元"]
    for unit in budget_units:
        if unit in text:
            match = re.search(r"(\d+(?:\.\d+)?)%s" % unit, text)
            if match:
                budget_value = float(match.group(1))
                if unit == "万": budget_value *= 10000
                entities["budget"] = int(budget_value)
    
    # 3. 作物类型提取：从9种作物中匹配
    crop_keywords = ["小麦", "水稻", "玉米", "棉花", "蔬菜", "果树", "大豆", "花生", "油菜"]
    for crop in crop_keywords:
        if crop in text:
            entities["crop_type"] = crop
            break  # 取第一个匹配的作物
    
    # 4. 地点提取：从CHINA_COORDS中匹配最长key
    best_loc = ""
    best_len = 0
    for loc in CHINA_COORDS.keys():
        if loc in text and len(loc) >= best_len and len(loc) > 0:
            best_loc = loc
            best_len = len(loc)
    if best_loc:
        entities["location"] = best_loc
    
    return entities
```

### 第2步：实体累积合并

在 `analyze()` 方法中合并历史实体（`requirement.py:101`）：

```python
async def analyze(self, user_input: str, accumulated: dict = None) -> Dict[str, Any]:
    entities = self._extract_entities(user_input)
    
    # 历史实体作为兜底（本轮未提取到的使用历史值）
    if accumulated:
        for k, v in accumulated.items():
            if v and not entities.get(k):
                entities[k] = v
    
    location = entities.get("location", "北京")  # 默认北京
    area = entities.get("area", 1000)            # 默认1000亩
    budget = entities.get("budget", 1000000)     # 默认100万
```

### 第3步：作物自动推断

如果未提取到作物类型，使用 `REGION_CROP_MAP` 推断（`requirement.py:12-44`）：

```python
REGION_CROP_MAP = {
    "黑龙江": "水稻", "吉林": "玉米", "辽宁": "玉米", "北京": "小麦", "天津": "小麦",
    "河北": "小麦", "山西": "小麦", "山东": "小麦", "河南": "小麦", "陕西": "小麦",
    "甘肃": "小麦", "宁夏": "小麦", "青海": "小麦", "新疆": "棉花", "内蒙古": "玉米",
    "江苏": "水稻", "上海": "水稻", "浙江": "水稻", "安徽": "水稻", "江西": "水稻",
    "湖北": "水稻", "湖南": "水稻", "福建": "水稻", "广东": "水稻", "广西": "水稻",
    "海南": "水稻", "四川": "水稻", "重庆": "水稻", "贵州": "水稻", "云南": "水稻",
    "西藏": "小麦",
}

def _infer_crop_by_location(location: str) -> Tuple[str, str]:
    for region, crop in REGION_CROP_MAP.items():
        if region in location:
            return crop, region
    return "", ""
```

调用逻辑（`requirement.py:113`）：
```python
crop_type = entities.get("crop_type")
crop_inferred = False
if not crop_type:
    inferred_crop, matched_region = _infer_crop_by_location(location)
    if inferred_crop:
        crop_type = inferred_crop
        crop_inferred = True
    else:
        crop_type = "小麦"  # 无法推断时回退为小麦
```

### 第4步：并行获取环境数据

调用 `DataAcquisition` 获取气象和土壤数据（`requirement.py:125-126`）：

```python
weather_data = await self.data_acquisition.get_weather_data(location)
soil_data = await self.data_acquisition.get_soil_data(location)
```

**气象数据** (`backend/data_acquisition/main.py:185`):
- API: `https://api.open-meteo.com/v1/forecast`
- 参数: `current=temperature_2m,relative_humidity_2m,wind_speed_10m,et0_fao_evapotranspiration`
- 输出字段: `temperature`(℃), `humidity`(%), `wind_speed`(m/s), `et0`(mm/d), `source`="Open-Meteo API (实时)"

**土壤数据** (`backend/data_acquisition/main.py:225`):
- API: `https://api.open-meteo.com/v1/forecast`
- 参数: `hourly=soil_temperature_0cm,soil_temperature_18cm,soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm`
- 土壤类型推断 (`_classify_from_moisture`, line 278): moisture>0.4→粘土, <0.2→砂土, >0.3→壤土, 其他→粉壤土
- 输出字段: `soil_type`, `soil_temperature`, `surface_moisture`, `field_capacity`, `wilting_point`, `available_water`, `source`="Open-Meteo API"

### 第5步：数据库参数查询

从MySQL查询作物和土壤参数，查询失败时使用硬编码默认值（`requirement.py:188-228`）：

```python
def _get_crop_parameters(self, crop_name: str) -> Dict[str, Any]:
    crop = self.db.query(CropParameter).filter(CropParameter.crop_name == crop_name).first()
    if crop:
        return {"stage_1_kc": crop.stage_1_kc, "stage_2_kc": crop.stage_2_kc, ...}
    
    # 默认值
    default_params = {
        "小麦": {"stage_1_kc": 0.3, "stage_2_kc": 0.5, "stage_3_kc": 1.15, "stage_4_kc": 0.4, 
                 "root_depth": 0.8, "stage_1_days": 30, "stage_2_days": 45, "stage_3_days": 60, "stage_4_days": 30},
        "水稻": {"stage_1_kc": 1.1, "stage_2_kc": 1.2, "stage_3_kc": 1.15, "stage_4_kc": 0.9,
                 "root_depth": 0.6, "stage_1_days": 25, "stage_2_days": 50, "stage_3_days": 45, "stage_4_days": 20},
        "玉米": {"stage_1_kc": 0.3, "stage_2_kc": 0.5, "stage_3_kc": 1.1, "stage_4_kc": 0.5,
                 "root_depth": 1.0, "stage_1_days": 25, "stage_2_days": 35, "stage_3_days": 45, "stage_4_days": 20}
    }
    return default_params.get(crop_name, default_params["小麦"])

def _get_soil_parameters(self, soil_type: str) -> Dict[str, Any]:
    soil = self.db.query(SoilParameter).filter(SoilParameter.soil_type == soil_type).first()
    if soil:
        return {"field_capacity": soil.field_capacity, "wilting_point": soil.wilting_point, ...}
    
    default_soil = {
        "壤土": {"field_capacity": 0.32, "wilting_point": 0.12, "available_water": 0.20},
        "砂土": {"field_capacity": 0.18, "wilting_point": 0.06, "available_water": 0.12},
        "粘土": {"field_capacity": 0.42, "wilting_point": 0.18, "available_water": 0.24}
    }
    return default_soil.get(soil_type, default_soil["壤土"])
```

### 第6步：构建项目概况

返回 `project_profile` 字典（`requirement.py:131`）：

```python
return {
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
```

## 决策标准

| 场景 | 决策 |
|-----|------|
| 作物已提取 | 使用用户指定的作物 |
| 作物未提取 + location可匹配REGION_CROP_MAP | 自动推断 + `crop_inferred=True` |
| 作物未提取 + location无法匹配 | 回退为"小麦" |
| Open-Meteo API调用失败 | 返回默认值（temperature=25, humidity=60, et0=3.5, soil_type=壤土） |
| 数据库查询失败 | 使用硬编码的默认作物/土壤参数（小麦Kc、壤土参数） |
| 坐标未匹配到CHINA_COORDS | 降级使用北京(39.9042, 116.4074) |

## 工具使用

| 工具 | 调用方式 | 输入 | 输出 |
|-----|---------|------|------|
| `DataAcquisition.get_weather_data()` | async调用 | location | {"temperature", "humidity", "wind_speed", "et0", ...} |
| `DataAcquisition.get_soil_data()` | async调用 | location | {"soil_type", "soil_temperature", "surface_moisture", ...} |
| `CHINA_COORDS`字典 | 本地查找 | location字符串 | (lat, lon)，覆盖150+城市 |
| `MySQL CropParameter` | SQLAlchemy query | crop_name | Kc系数/根深/天数 |
| `MySQL SoilParameter` | SQLAlchemy query | soil_type | 田间持水量/萎蔫系数 |

## 质量检查

在输出 project_profile 前确认：
- [ ] location 和 crop_type 不为空（允许推断值）
- [ ] area 为float类型，单位统一为亩
- [ ] budget 为int类型，单位统一为元
- [ ] weather_data 包含 temperature/humidity/wind_speed/et0 四个字段
- [ ] soil_data 包含 soil_type/field_capacity/wilting_point/available_water
- [ ] crop_parameters 包含 4个生育期的Kc值 和 stage_days
- [ ] crop_inferred 标记正确（用户指定为False，推断为True）

> **代码位置**: `backend/agents/requirement.py`
> **核心方法**: `analyze()`(line 101), `_extract_entities()`(line 146), `_get_crop_parameters()`(line 188), `_get_soil_parameters()`(line 212)
> **依赖**: `REGION_CROP_MAP`(line 12), `DataAcquisition`(line 57)