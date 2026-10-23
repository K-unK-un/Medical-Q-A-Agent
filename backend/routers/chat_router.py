"""对话路由模块

接口：
- /chat/stream: 首次对话流式接口，处理用户问题并返回流式响应
- /chat/resume: 恢复被中断的对话（interrupt），继续流式输出
- /chat/map: 查询地图周边的医院
"""
import json
import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from triage_agent.graph import stream_triage, resume_triage
from utils.logger import setup_logger

logger = setup_logger(__name__)

# 定义对话路由，统一前缀为 /chat
chat_router = APIRouter(prefix="/chat")


async def _stream(question: str, thread_id: str):
    """内部流式处理函数
    调用 triage 状态图进行分诊处理，将每一步结果序列化为 JSON 字符串
    通过生成器方式逐行 yield，供 StreamingResponse 使用。
    返回的 chunk 格式：
    - {c: "文本内容"}  正常流式内容
    - {interrupt: true, thread_id: "..."}  中断信号（需用户确认）
    - {err: "错误信息"}  异常情况
    """
    try:
        # 调用分诊状态图的流式接口，逐块 yield 结果
        async for chunk in stream_triage(question, thread_id):
            yield json.dumps(chunk, ensure_ascii=False) + "\n"
    except Exception as e:
        # 捕获异常并记录日志，同时向前端返回错误信息
        logger.error(f"stream error: {e}")
        yield json.dumps({"err": str(e)}, ensure_ascii=False) + "\n"


@chat_router.get("/stream")
async def stream_chat(question: str = Query(...), thread_id: str = Query(default=None)):
    """首次对话流式接口
    流中可能出现的信号：
    - {c:"文本"}  正常流式内容
    - {interrupt:true, thread_id:"..."}  中断信号，前端收到后展示确认按钮
    - {err:"错误信息"}  异常情况
    """
    # 若未提供 thread_id，则生成新的 UUID 作为对话标识
    if not thread_id:
        thread_id = str(uuid.uuid4())
    # 返回流式响应，客户端通过逐行解析获取内容
    return StreamingResponse(_stream(question, thread_id))


class ResumeRequest(BaseModel):
    thread_id: str
    resume_value: str


async def _resume(thread_id: str, resume_value: str):
    """恢复对话的内部处理函数"""
    try:
        async for chunk in resume_triage(thread_id, resume_value):
            yield json.dumps(chunk, ensure_ascii=False) + "\n"
    except Exception as e:
        logger.error(f"resume error: {e}")
        yield json.dumps({"err": str(e)}, ensure_ascii=False) + "\n"


@chat_router.post("/resume")
async def resume_chat(req: ResumeRequest):
    """恢复被中断的对话"""
    return StreamingResponse(_resume(req.thread_id, req.resume_value))


