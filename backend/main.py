from contextlib import asynccontextmanager

from fastapi import FastAPI

from routers.chat_router import chat_router
from utils.logger import setup_logger

import uvicorn

logger = setup_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("健康分诊助手启动")
    yield
    logger.info("健康分诊助手关闭")


app = FastAPI(lifespan=lifespan, title="健康分诊助手 API")
app.include_router(chat_router, prefix="/api")


@app.get("/")
async def root():
    return {"msg": "健康分诊助手 API 正在运行"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
