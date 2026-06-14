import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, AsyncGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DeepSeekLLM:
    MODE_API = "api"
    MODE_NONE = "none"

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.api_client = None
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.api_base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
        self.api_model = os.environ.get("DEEPSEEK_MODEL_NAME", "deepseek-chat")
        self._loaded = False
        self._mode = self.MODE_NONE

    def load(self) -> bool:
        if self._loaded:
            return True

        if self._try_load_api():
            self._mode = self.MODE_API
            self._loaded = True
            logger.info(f"[DeepSeek] API客户端已连接: {self.api_base}")
            return True

        logger.warning("[DeepSeek] API不可用")
        return False

    def _try_load_api(self) -> bool:
        if not self.api_key or self.api_key == "your_api_key":
            logger.warning("[DeepSeek] API密钥未配置")
            return False
        try:
            from openai import OpenAI
            self.api_client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
            )
            models = self.api_client.models.list()
            logger.info(f"[DeepSeek] API可用模型: {[m.id for m in models.data[:5]]}")
            return True
        except ImportError:
            logger.warning("[DeepSeek] openai 库未安装")
            return False
        except Exception as e:
            logger.warning(f"[DeepSeek] API连接失败: {e}")
            return False

    @property
    def mode(self) -> str:
        return self._mode

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        if not self._loaded:
            return ""

        if self._mode == self.MODE_API:
            return self._generate_api(prompt, max_tokens)
        return ""

    async def generate_async(self, prompt: str, max_tokens: int = 1024) -> str:
        if not self._loaded:
            return ""
        if self._mode == self.MODE_API:
            return await asyncio.to_thread(self._generate_api, prompt, max_tokens)
        return ""

    def _generate_api(self, prompt: str, max_tokens: int) -> str:
        try:
            response = self.api_client.chat.completions.create(
                model=self.api_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.3,
                top_p=0.9,
            )
            text = response.choices[0].message.content or ""
            logger.info(f"[DeepSeek][API] 生成完成, {len(text)} 字符")
            return text.strip()
        except Exception as e:
            logger.error(f"[DeepSeek][API] 生成失败: {e}")
            return ""

    async def generate_stream(self, prompt: str, max_tokens: int = 1024) -> AsyncGenerator[str, None]:
        if not self._loaded:
            yield ""
            return

        if self._mode == self.MODE_API:
            async for chunk in self._generate_stream_api(prompt, max_tokens):
                yield chunk
        else:
            yield ""

    async def _generate_stream_api(self, prompt: str, max_tokens: int) -> AsyncGenerator[str, None]:
        try:
            def _create_stream():
                return self.api_client.chat.completions.create(
                    model=self.api_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.3,
                    top_p=0.9,
                    stream=True,
                )
            stream = await asyncio.to_thread(_create_stream)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"[DeepSeek][API] 流式生成失败: {e}")
            yield ""


deepseek = DeepSeekLLM()


def build_irrigation_prompt(
    profile: Dict[str, Any],
    knowledge_refs: list,
    conversation_context: str = "",
    design_knowledge: list = None,
    equip_knowledge: list = None,
    template_refs: list = None,
    tech_refs: list = None,
    standard_refs: list = None,
) -> str:
    location = profile.get("location", "未知")
    crop = profile.get("crop_type", "未知")
    area = profile.get("area", 0)
    budget = profile.get("budget", 0)
    crop_inferred = profile.get("crop_inferred", False)
    weather = profile.get("weather_data", {})
    soil = profile.get("soil_data", {})
    crop_params = profile.get("crop_parameters", {})

    def _format_refs(refs, max_items=3, max_chars=400):
        if not refs:
            return "（无）"
        seen = set()
        parts = []
        for r in refs:
            if isinstance(r, dict):
                key = str(r.get("source", "")) + str(r.get("content", ""))[:80]
            else:
                key = str(r)[:80]
            if key in seen:
                continue
            seen.add(key)
            src = r.get("source", "") if isinstance(r, dict) else ""
            content = r.get("content", str(r))[:max_chars] if isinstance(r, dict) else str(r)[:max_chars]
            parts.append(f"  来源: {src}\n  内容: {content}")
            if len(parts) >= max_items:
                break
        return "\n".join(parts)

    template_text = _format_refs(template_refs or [], max_items=3)
    tech_text = _format_refs(tech_refs or [], max_items=4)
    standard_text = _format_refs(standard_refs or [], max_items=3)

    all_knowledge = (template_refs or []) + (tech_refs or []) + (standard_refs or [])
    all_knowledge += (design_knowledge or []) + (equip_knowledge or [])
    all_knowledge += (knowledge_refs or [])
    seen_content = set()
    flat_knowledge_text = ""
    count = 0
    for ref in all_knowledge:
        if isinstance(ref, dict):
            key = str(ref.get("source", "")) + str(ref.get("content", ""))[:100]
        else:
            key = str(ref)[:100]
        if key in seen_content:
            continue
        seen_content.add(key)
        count += 1
        k_content = ref.get("content", str(ref))[:200] if isinstance(ref, dict) else str(ref)[:200]
        k_source = ref.get("source", "") if isinstance(ref, dict) else ""
        flat_knowledge_text += f"  [{count}] {k_source}\n      {k_content}\n"
        if count >= 6:
            break

    crop_note = "（系统根据地区自动推断）" if crop_inferred else ""

    return f"""你是一个智慧灌溉方案设计专家。请按照"先搭框架→再填内容→再做校验"的三步流程生成方案。

## 第一步：搭框架 —— 从政府规划文件中提取章节结构

以下是知识库中检索到的【方案结构模板】，来源于真实的政府灌溉规划文件。
请研读这些模板，提取它们共有的章节结构（如"规划背景→总体布局→建设任务→投资估算→保障措施"），
以此为框架来组织方案，而不是用你自己的固定格式。

### 【方案结构模板】（此通道检索自政府规划文件，用于确定方案框架）
{template_text}

## 第二步：填内容 —— 从学术论文和技术文献中提取技术方法

以下是从知识库中检索到的【技术参数和方法】，来源于学术论文和技术文献。
请在上述框架的每章中，定向填入相关的技术参数、计算方法和量化数据。

### 【技术参数和方法】（此通道检索自学术论文/技术文献，用于技术填充）
{tech_text}

## 第三步：做校验 —— 用国标/行标条款做合规比对

以下是从知识库中检索到的【合规标准】，来源于国标/行标。
请将方案中的数据与这些标准条款逐一比对，确保合规。

### 【合规标准】（此通道检索自国标/行标，用于合规校验）
{standard_text}

## 项目基本信息
- 地点: {location}
- 作物: {crop}{crop_note}
- 面积: {area}亩
- 预算: {budget:,}元

## 实时气象数据
- 温度: {weather.get('temperature','N/A')}°C
- 湿度: {weather.get('humidity','N/A')}%
- 参考ET0: {weather.get('et0','N/A')} mm/天

## 土壤数据
- 类型: {soil.get('soil_type','N/A')}
- 田间持水量: {soil.get('field_capacity','N/A')}

## 作物参数
{json.dumps(crop_params, ensure_ascii=False, indent=2)}

## 补充参考（打平的全部知识参考）
{flat_knowledge_text if flat_knowledge_text else '无'}

## 对话上下文
{conversation_context if conversation_context else '无'}

## 输出要求
1. 方案标题中的年份必须使用当前年份：{datetime.now().year}
2. 章节结构必须参考【方案结构模板】中的政府规划文件框架
3. 每章的技术参数必须引用【技术参数和方法】中的具体数据
4. 方案末尾加入"合规性校验"，引用【合规标准】中的标准编号和指标要求
5. 每个关键结论标注知识来源（文档名）
6. 只输出方案内容，不要任何解释性前缀。"""


def build_quality_prompt(plan_content: str, profile: Dict[str, Any]) -> str:
    return f"""你是一个灌溉方案质量审核专家。请审核以下方案的质量。

## 方案内容
{plan_content[:3000]}

## 项目背景
- 地点: {profile.get('location','')}
- 作物: {profile.get('crop_type','')}
- 面积: {profile.get('area','')}亩

请从以下三个维度评分（每项0-100分），并以JSON格式输出结果：
1. 合规性(compliance): 是否符合国标GB/T 50363-2018等规范
2. 合理性(rationality): 计算方法、参数选择是否合理
3. 充分性(sufficiency): 方案内容是否完整

输出格式（纯JSON，不要夹带任何其他文字）：
{{"compliance": {{"score": 85, "details": ["原因1", "原因2"]}}, "rationality": {{"score": 78, "details": ["原因1"]}}, "sufficiency": {{"score": 90, "details": ["原因1"]}}, "overall_score": 84, "grade": "良好"}}"""


def build_conversation_reply(user_input: str, context: str, data: dict) -> str:
    ctx = "\n".join(context[-5:]) if context else "无"
    return f"""你是智慧灌溉助手。根据当前对话上下文回复用户。

## 对话历史
{ctx}

## 当前数据
{json.dumps(data, ensure_ascii=False, indent=2)}

## 用户消息
{user_input}

请简洁自然地回复用户。如果是方案生成后，用表格总结关键参数。"""


def log_json(label: str, data: Any):
    try:
        s = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        logger.info(f"\n{'='*60}\n[JSON] {label}\n{'='*60}\n{s}\n{'='*60}")
    except Exception:
        logger.info(f"[JSON] {label}: {str(data)[:500]}")