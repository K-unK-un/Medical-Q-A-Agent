"""RAG 知识检索：向量检索 + CrossEncoder 重排序。
复用 rag_server 的 VectorRetriever + CompressReranker 方案，改为按科室检索。"""

import os

from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder

from models.chroma import get_vectorstore
from utils.env_util import hf_home
from utils.logger import setup_logger

logger = setup_logger(__name__)

_DEPT_NAME = {
    "internal_medicine": "内科",
    "surgery": "外科",
    "pediatrics": "儿科",
}

# reranker 交叉编码器模型本地路径（bge-reranker-large）
_RERANKER_MODEL_PATH = os.path.join(
    hf_home,
    "hub",
    "models--BAAI--bge-reranker-large",
    "snapshots",
    "55611d7bca2a7133960a6d3b71e083071bbfc312",
)

# reranker 单例（避免每次检索都重新加载模型）
_reranker_compressor = None


def _get_reranker() -> CrossEncoderReranker:
    """获取 reranker 压缩器（单例）。"""
    global _reranker_compressor
    if _reranker_compressor is None:
        cross_encoder = HuggingFaceCrossEncoder(model_name=_RERANKER_MODEL_PATH)
        _reranker_compressor = CrossEncoderReranker(model=cross_encoder, top_n=3)
    return _reranker_compressor


def retrieve_knowledge(query: str, department: str) -> str:
    """RAG 检索：向量检索 + CrossEncoder 重排序。
    返回最相关的医学知识文本。"""
    # 获取该科室的向量库（首次自动索引）
    vs = get_vectorstore(department)

    # 第一步：向量检索器（similarity_score_threshold，分数 >= 0.5）
    retriever = vs.as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={"k": 5, "score_threshold": 0.5},
    )

    # 第二步：用 reranker 压缩检索器包装
    reranker = _get_reranker()
    compress_retriever = ContextualCompressionRetriever(
        base_retriever=retriever,
        base_compressor=reranker,
    )

    # 执行检索 + 重排序
    chunks = compress_retriever.invoke(query)

    if not chunks:
        return "暂无相关医学资料。"

    # 拼接为纯文本
    context = "\n\n".join(doc.page_content for doc in chunks)
    logger.info(f"科室 [{department}] 检索到 {len(chunks)} 个相关文档块")
    return context


def get_dept_name(department: str) -> str:
    return _DEPT_NAME.get(department, "健康")
