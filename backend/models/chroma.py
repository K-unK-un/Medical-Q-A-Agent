"""Chroma 向量数据库，按科室分 collection。
"""

import os
from typing import Dict

from langchain_chroma import Chroma
from langchain_community.document_loaders import UnstructuredMarkdownLoader
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from utils.env_util import embeddings_model_name, chroma_host, chroma_port
from utils.logger import setup_logger

logger = setup_logger(__name__)

# 全局 embeddings 实例（Ollama 本地模型）
embeddings = OllamaEmbeddings(model=embeddings_model_name)

# 知识库 MD 文件目录
_KB_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")

# 科室 → MD 文件名
_DEPT_FILE = {
    "internal_medicine": "internal_medicine.md",
    "surgery": "surgery.md",
    "pediatrics": "pediatrics.md",
}

# collection 缓存：科室 → Chroma 实例
_vectorstores: Dict[str, Chroma] = {}


def _create_vectorstore(department: str) -> Chroma:
    """创建指定科室的 Chroma collection。"""
    return Chroma(
        host=chroma_host,
        port=chroma_port,
        embedding_function=embeddings,
        collection_name=department,
        collection_metadata={"hnsw:space": "cosine"},
    )


def _get_vectorstore(department: str) -> Chroma:
    """获取指定科室的 Chroma 实例（带缓存）。"""
    if department not in _vectorstores:
        _vectorstores[department] = _create_vectorstore(department)
    return _vectorstores[department]


def _load_and_index(department: str):
    """从 MD 文件加载文档，分割后索引到向量库。"""
    filename = _DEPT_FILE.get(department)
    if not filename:
        logger.warning(f"未知科室：{department}")
        return

    filepath = os.path.join(_KB_DIR, filename)
    if not os.path.exists(filepath):
        logger.warning(f"知识库文件不存在：{filepath}")
        return

    # 加载 MD 文档
    loader = UnstructuredMarkdownLoader(file_path=filepath)
    docs = loader.load()

    # 分割文档
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=512,
        chunk_overlap=50,
        separators=["\n\n", "\n", "。", "？"],
    )
    chunks = splitter.split_documents(docs)

    # 索引到向量库（先清空旧数据）
    vs = _get_vectorstore(department)
    existing_ids = vs.get()["ids"] # 向量库中已有文档 ID
    if existing_ids:
        vs.delete(ids=existing_ids)
    vs.add_documents(chunks)

    logger.info(f"索引 {filename} 完成，共 {len(chunks)} 个文档块")


def ensure_indexed(department: str):
    """确保科室知识库已索引（首次调用时自动从 MD 加载）。"""
    vs = _get_vectorstore(department)
    count = len(vs.get()["ids"]) # 向量库中已有文档数量
    if count == 0:
        logger.info(f"科室 [{department}] 知识库为空，开始索引...")
        _load_and_index(department)


def get_vectorstore(department: str) -> Chroma:
    """获取已索引的向量库（自动确保已索引）。"""
    ensure_indexed(department)
    return _get_vectorstore(department)
