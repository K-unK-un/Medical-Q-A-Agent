"""健康分诊状态图
"""
from typing import Literal

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import START, END
from langgraph.graph import MessagesState, StateGraph
from langgraph.types import interrupt, Command
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from models.llm import qwen_llm
from tools.nearby_search_tool import nearby_search_tool
from triage_agent.knowledge import retrieve_knowledge, get_dept_name
from utils.env_util import connection_string
from utils.logger import setup_logger

logger = setup_logger(__name__)

# 危重症状关键词（规则引擎，强制拦截，避免 LLM 误判）
EMERGENCY_KEYWORDS = [
    "呼吸困难", "意识不清", "昏迷", "大出血", "休克",
    "抽搐", "中毒", "心脏骤停", "窒息", "剧烈腹痛", "晕厥",
    "吐血", "高烧不退",
]

# 只流式输出这些节点的 LLM 结果（过滤 classify 的中间分类输出）
_STREAM_NODES = {"internal_medicine", "surgery", "pediatrics", "chat", "emergency", "map", "disclaimer", "doctor_node"}


# 共享状态：复用 MessagesState（自动管理 messages 追加），扩展分类与危重标记
class TriageState(MessagesState):
    # 分类结果
    classify_result: Literal["internal_medicine", "surgery", "pediatrics", "chat", "map"]
    # 危重标记（规则引擎硬判断）
    is_emergency: bool
    # ESI 五级分诊结果（LLM 细粒度评估）
    triage_level: int
    # 安全审查结果
    safety_action: Literal["pass", "append_disclaimer"]
    safety_issues: list[str]
    # 症状充分性 + 追问计数
    sufficient: bool
    missing_info: list[str]
    followup_count: int
    # 用户从下拉框选择的城市名（map_node interrupt resume 后写入）
    selected_city: str


# Pydantic 输出校验
class Classify(BaseModel):
    classify_name: Literal["internal_medicine", "surgery", "pediatrics", "chat", "map"] = Field(
        description="分类结果：internal_medicine内科、surgery外科、pediatrics儿科、chat健康咨询、map地址信息"
    )


# ESI 五级分诊结构化输出
class TriageAssessment(BaseModel):
    level: int = Field(ge=1, le=5, description="ESI分诊级别1-5：1立即抢救、2急诊、3紧急、4次紧急、5非紧急")


# 安全审查结构化输出
class SafetyReview(BaseModel):
    is_safe: bool = Field(description="回复是否安全")
    issues: list[str] = Field(default_factory=list, description="发现的具体安全问题")
    action: Literal["pass", "append_disclaimer"] = Field(
        description="pass放行原回复；append_disclaimer追加安全警示"
    )
    sufficient: bool = Field(description="用户症状信息是否足以判断疾病")
    missing_info: list[str] = Field(default_factory=list, description="缺失的关键信息，用于反问补充")


# 获取用户最新消息
def _get_current_question(state: TriageState) -> str:
    messages = state["messages"]
    human_messages = [m for m in messages if isinstance(m, HumanMessage)]
    return human_messages[-1].content


def _get_all_question(state: TriageState) -> str:
    return "".join(m.content for m in state["messages"] if isinstance(m, HumanMessage))


# 节点1：分类（LLM 判断科室）
def classify_node(state: TriageState):
    out_parser = PydanticOutputParser(pydantic_object=Classify)
    question = _get_current_question(state)

    template = """你是一个医疗分诊助手，根据用户描述的症状判断所属科室，如果用户输入了地址信息(必须输入了地址信息才算，比如:我在哪这中信息不算)则分类到地址,。
用户问题：{question}
分类结果：internal_medicine（内科）、surgery（外科）、pediatrics（儿科）、chat（健康咨询/科普）
示例：
问题：我头疼发烧三天了  回答：internal_medicine
问题：摔伤了腿伤口很深  回答：surgery
问题：孩子一直咳嗽  回答：pediatrics
问题：感冒多喝水有用吗  回答：chat
问题：我在四川宜宾  回答：map
{format_instructions}"""
    prompt = PromptTemplate(
        template=template,
        input_variables=["question"],
        partial_variables={"format_instructions": out_parser.get_format_instructions()},
    )
    chain = prompt | qwen_llm | out_parser
    result = chain.invoke({"question": question})
    logger.info(f"分类结果：{result.classify_name}")
    return {"classify_result": result.classify_name}


# 节点2：危重拦截
def emergency_check_node(state: TriageState):
    question = _get_current_question(state)
    is_emergency = any(kw in question for kw in EMERGENCY_KEYWORDS)  # 判断关键词是否在字符串中出现
    logger.info(f"危重检测：is_emergency={is_emergency}")

    parser = PydanticOutputParser(pydantic_object=TriageAssessment)
    question = _get_all_question(state)
    template = """你是急诊分诊护士，使用 ESI 五级分诊标准评估患者紧急程度。
分级规则：
1=立即抢救（生命体征不稳定，如昏迷/休克/心脏骤停）
2=急诊（危重但生命体征稳定，10分钟内，如胸痛/呼吸困难/剧烈腹痛）
3=紧急（30分钟内，如高热/持续呕吐）
4=次紧急（60分钟内，如轻症感染）
5=非紧急（常规就诊/科普，如健康咨询）
患者描述：{question}
请输出分诊级别。
{format_instructions}"""
    prompt = PromptTemplate(
        template=template,
        input_variables=["question"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )
    result = (prompt | qwen_llm | parser).invoke({"question": question})
    logger.info(f"分诊评分：level={result.level}")

    return {"triage_level": result.level, "is_emergency": is_emergency}


# 节点2.5：ESI 五级分诊评分（LLM 细粒度评估，作为规则硬墙之外的补充）


# 节点3：急诊处理（interrupt 暂停，等待用户确认）
async def emergency_node(state: TriageState):
    advice = "检测到您可能存在危重症状，建议立即就医或拨打120。"
    user_decision = interrupt({"type": "emergency", "advice": advice})

    if user_decision == "confirm":
        prompt_text = "用户确认需要紧急帮助。请用温暖专业的语气给出简短行动指引：立即拨打120、保持冷静、记录症状变化、保持通讯畅通。"

        full_content = ""
        async for chunk in qwen_llm.astream([HumanMessage(content=prompt_text)]):
            full_content += chunk.content

        return Command(
            goto="map",
            update={"messages": [AIMessage(content=full_content)]}
        )
    else:
        return Command(
            goto=state.get("classify_result", "chat"),
            update={}
        )


# 科室节点通用逻辑（RAG 检索 + LLM 生成）
async def _department_node(state: TriageState, department: str):
    question = _get_all_question(state)
    docs = retrieve_knowledge(question, department)
    level = state.get("triage_level", 5)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是{dept}助手，基于以下医学资料回答用户问题。"
                   "当前ESI分诊级别{level}（1最紧急-5非紧急），"
                   "请按紧急程度调整语气：1-2级优先提示立即就医，3级提示尽快就诊，4-5级可科普。"
                   "若资料不足以判断，请明确说明并建议就医，不要编造诊断。\n资料：\n{docs}"),
        ("human", "{question}"),
    ])

    full_content = ""
    async for chunk in qwen_llm.astream(
            prompt.format_messages(dept=get_dept_name(department), docs=docs, question=question, level=level)):
        full_content += chunk.content

    return {"messages": [AIMessage(content=full_content)]}


async def internal_medicine_node(state: TriageState):
    return await _department_node(state, "internal_medicine")


async def surgery_node(state: TriageState):
    return await _department_node(state, "surgery")


async def pediatrics_node(state: TriageState):
    return await _department_node(state, "pediatrics")


# 健康咨询节点（直接用历史消息对话）
async def chat_node(state: TriageState):
    full_content = ""
    async for chunk in qwen_llm.astream(state["messages"]):
        full_content += chunk.content

    return {"messages": [AIMessage(content=full_content)]}


# 节点：安全审查（第二 LLM，复核上一节点的医疗回复；同时判断症状充分性，不流式输出）
def safety_review_node(state: TriageState):
    last_ai = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
    if not last_ai:
        return {"safety_action": "pass", "safety_issues": [], "sufficient": True, "missing_info": []}
    advice = last_ai.content
    symptoms = _get_all_question(state)
    parser = PydanticOutputParser(pydantic_object=SafetyReview)
    template = """你是医疗安全审查员（第二审查 LLM），综合用户症状与 AI 回复做两项判断：

【判断一】症状充分性
结合用户描述：{symptoms}
判断现有症状信息是否足以明确判断疾病。若不足，列出缺失的关键信息（如病程长短、伴随症状、诱发因素、既往史、年龄性别等）。

【判断二】回复安全性
审查以下 AI 回复是否存在：
1. 用药剂量/频次/疗程错误
2. 禁忌症遗漏（如孕妇、儿童用药不当）
3. 危重症状未提示就医
4. 过度诊断（给出确定性诊断而非建议就医明确）
5. 缺失免责声明
仅当存在明确问题时才标记不安全，无问题则 pass。

AI 回复：{advice}
{format_instructions}"""
    prompt = PromptTemplate(
        template=template,
        input_variables=["symptoms", "advice"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )
    review = (prompt | qwen_llm | parser).invoke({"symptoms": symptoms, "advice": advice})
    logger.info(f"安全审查：sufficient={review.sufficient} missing={review.missing_info} "
                f"is_safe={review.is_safe} issues={review.issues} action={review.action}")
    return {
        "safety_action": review.action,
        "safety_issues": review.issues,
        "sufficient": review.sufficient,
        "missing_info": review.missing_info,
    }


# 节点：安全警示生成（仅当审查发现问题，流式输出警示）
async def disclaimer_node(state: TriageState):
    issues = state.get("safety_issues", [])
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是医疗安全审查员，发现 AI 回复存在以下问题：{issues}。"
                   "请用专业简短的语气生成一条安全警示，提示该问题并建议就医确认，开头加 ⚠️。"),
    ])
    full_content = ""
    async for chunk in qwen_llm.astream(prompt.format_messages(issues="；".join(issues))):
        full_content += chunk.content
    return {"messages": [AIMessage(content=full_content)]}


# 节点：症状不足时反问用户补充（interrupt 暂停），resume 后带补充症状回到科室节点重新生成
async def followup_node(state: TriageState):
    missing = state.get("missing_info", [])

    prompt_text = "为更准确判断，请补充更多的症状细节：" + ("；".join(missing) if missing else "更多症状细节")
    user_input = interrupt({"type": "followup", "prompt": prompt_text})
    logger.info(f"追问补充：{user_input}，第{state.get('followup_count', 0) + 1}次")

    dept = state.get("classify_result")
    return Command(
        goto=dept,
        update={
            "messages": [HumanMessage(content=user_input)],
            "followup_count": state.get("followup_count", 0) + 1,
        },
    )


# 条件路由：症状不足且未超2次→反问；否则按安全action
def route_after_review(state: TriageState):
    if state.get("classify_result") == "chat":
        if state.get("safety_action") == "append_disclaimer":
            return "disclaimer"
    if not state.get("sufficient", True) and state.get("followup_count", 0) < 1:
        return "followup"
    if state.get("safety_action") == "append_disclaimer":
        return "disclaimer"
    return "doctor_node"


# 条件路由：规则危重或ESI≤2级→急诊；否则按分类进科室
def route_after_triage(state: TriageState):
    if state.get("is_emergency"):
        return "emergency"
    if state.get("triage_level", 5) <= 2:
        return "emergency"
    mapping = {
        "internal_medicine": "internal_medicine",
        "surgery": "surgery",
        "pediatrics": "pediatrics",
        "chat": "chat",
        "map": "map"
    }
    return mapping.get(state.get("classify_result"), "chat")


# 节点4：周边医院
async def map_node(state: TriageState):
    if state.get("is_emergency") or state.get("triage_level", 5) <= 2:
        # 急诊场景：中断让用户从前端下拉框选城市
        city = interrupt({"type": "address_select", "prompt": "请选择您所在的城市，我会为您推荐附近的医院"})
        logger.info(f"紧急节点，用户选择城市：{city}")

        hospitals = await nearby_search_tool.ainvoke({"city": city})

        # 格式化医院列表
        hospital_text = "\n".join(
            f"• {h['名称']}（{h['标签']}）\n  地址：{h['地址']}\n  营业时间：{h['营业时间']}"
            for h in hospitals
        ) or "未找到附近医院信息。"

        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "你是就医导航助手，用户正经历紧急情况，请用简洁专业的语气推荐附近医院，先推荐质量好的医院，再推荐附近的医院(无论好坏)。"),
            ("human", "请推荐医院。\n医院列表：\n{docs}"),
        ])

        full_content = ""
        async for chunk in qwen_llm.astream(
                prompt.format_messages(city=city, docs=hospital_text)
        ):
            full_content += chunk.content
        return Command(
            goto=END,
            update={"messages": [AIMessage(content=full_content)], "selected_city": city}
        )

    # 非急诊场景：从用户消息提取地址
    human_message = _get_current_question(state)

    extract_prompt = ChatPromptTemplate.from_messages([
        ("system", "从用户消息中提取城市或地点名称，只返回地址本身，不要加任何解释。"),
        ("human", "{question}"),
    ])
    chain = extract_prompt | qwen_llm
    address = chain.invoke({"question": human_message}).content.strip()
    logger.info(f"地图节点提取地址：{address}")

    hospitals = await nearby_search_tool.ainvoke({"city": address})

    hospital_text = "\n".join(
        f"• {h['名称']}（{h['标签']}）\n  地址：{h['地址']}\n  营业时间：{h['营业时间']}"
        for h in hospitals
    ) or "未找到附近医院信息。"

    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是就医导航助手，根据以下周边医院信息为用户推荐，语气简洁专业。"),
        ("human", "告诉我医院列表里面的所有医院, 包含地址标签营业时间。\n医院列表：\n{docs}"),
    ])

    full_content = ""
    async for chunk in qwen_llm.astream(
            prompt.format_messages(address=address, docs=hospital_text)
    ):
        full_content += chunk.content

    return {"messages": [AIMessage(content=full_content)]}


# 从数据库推荐医生节点
async def doctor_node(state: TriageState):
    dept = state.get("classify_result", "")
    # chat 健康咨询不推荐医生
    if dept == "chat":
        return
    dept_name = get_dept_name(dept)

    # 查数据库：该科室所有医生
    engine = create_engine(connection_string)
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT name, gender, age, practice_years, intro FROM doctors WHERE department=:dept"
        ), {"dept": dept})
        doctors = [dict(row._mapping) for row in result.fetchall()]
    engine.dispose()

    if not doctors:
        return

    # 格式化医生信息供 LLM 选择
    question = _get_all_question(state)
    doctors_info = "\n".join([
        f"{i + 1}. {d['name']}（{d['gender']}，{d['age']}岁，从医{d['practice_years']}年）：{d['intro']}"
        for i, d in enumerate(doctors)
    ])

    # LLM 根据病情推荐一位最合适的医生
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是医疗推荐助手。根据患者病情，从以下{dept}医生中推荐一位最合适的，"
                   "简短说明推荐理由（如擅长领域与病情匹配度）。"),
        ("human", "病情：{question}\n候选医生：\n{doctors}"),
    ])

    full_content = ""
    async for chunk in qwen_llm.astream(
            prompt.format_messages(dept=dept_name, question=question, doctors=doctors_info)):
        full_content += chunk.content

    return {"messages": [AIMessage(content=full_content)]}


def create_triage_graph():
    graph = StateGraph(TriageState)

    # 添加节点
    graph.add_node("classify", classify_node)
    graph.add_node("emergency_check", emergency_check_node)  # ESI 五级分诊评分
    graph.add_node("emergency", emergency_node)
    graph.add_node("internal_medicine", internal_medicine_node)
    graph.add_node("surgery", surgery_node)
    graph.add_node("pediatrics", pediatrics_node)
    graph.add_node("chat", chat_node)
    graph.add_node("map", map_node)
    graph.add_node("safety_review", safety_review_node)
    graph.add_node("disclaimer", disclaimer_node)  # 安全警示流式输出
    graph.add_node("followup", followup_node)  # 症状不足反问补充
    graph.add_node("doctor_node", doctor_node)  # 医生推荐

    # 添加边
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "emergency_check")
    graph.add_conditional_edges("emergency_check", route_after_triage)
    graph.add_edge("internal_medicine", "safety_review")
    graph.add_edge("surgery", "safety_review")
    graph.add_edge("pediatrics", "safety_review")
    graph.add_edge("chat", "safety_review")
    graph.add_conditional_edges("safety_review", route_after_review)
    graph.add_edge("disclaimer", "doctor_node")
    graph.add_edge("doctor_node", END)
    graph.add_edge("map", END)

    # checkpointer 保证 interrupt 中断状态可恢复
    return graph.compile(checkpointer=MemorySaver())


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = create_triage_graph()
    return _graph


def _is_interrupted(graph, config) -> bool:
    """检查图是否处于 interrupt 暂停状态。"""
    state = graph.get_state(config)
    if not state:
        return False
    return bool(state.next) and any(getattr(t, "interrupts", None) for t in state.tasks)


def _get_interrupt_payload(graph, config):
    """获取当前 interrupt 的 payload（如 {"type": "emergency", ...}）。"""
    state = graph.get_state(config)
    if not state:
        return None
    for task in state.tasks:
        interrupts = getattr(task, "interrupts", None)
        if interrupts:
            return interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
    return None


async def stream_triage(question: str, thread_id: str):
    """对话流式输出。若触发 interrupt，流结束后 yield interrupt 信号（含 type 区分中断类型）。"""
    graph = get_graph()
    config = RunnableConfig(configurable={"thread_id": thread_id})

    async for event in graph.astream_events(
            {"messages": [HumanMessage(content=question)]},
            config=config,
            version="v2",
    ):
        if event["event"] == "on_chat_model_stream":
            if event["metadata"].get("langgraph_node") in _STREAM_NODES:
                chunk = event["data"]["chunk"].content
                if chunk:
                    yield {"c": chunk}

    # 流结束后检查是否被 interrupt 暂停，把 type 传给前端区分渲染
    payload = _get_interrupt_payload(graph, config)
    if payload:
        yield {"interrupt": True, "thread_id": thread_id, "type": payload.get("type", ""), "payload": payload}


async def resume_triage(thread_id: str, resume_value: str):
    """恢复被 interrupt 暂停的图，继续流式输出。流结束后若再次被中断，yield interrupt 信号。"""
    graph = get_graph()
    config = RunnableConfig(configurable={"thread_id": thread_id})

    async for event in graph.astream_events(
            Command(resume=resume_value),
            config=config,
            version="v2",
    ):
        if event["event"] == "on_chat_model_stream":
            if event["metadata"].get("langgraph_node") in _STREAM_NODES:
                chunk = event["data"]["chunk"].content
                if chunk:
                    yield {"c": chunk}

    # resume 后可能又触发了第二次 interrupt（如急诊确认后进入 map_node 再中断选地址）
    payload = _get_interrupt_payload(graph, config)
    if payload:
        yield {"interrupt": True, "thread_id": thread_id, "type": payload.get("type", ""), "payload": payload}
