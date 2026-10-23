import os
from dotenv import load_dotenv

load_dotenv()

qwen_model_name = os.getenv("QWEN_MODEL_NAME", "")
qwen_api_key = os.getenv("QWEN_API_KEY", "")
qwen_base_url = os.getenv("QWEN_BASE_URL", "")

gaode_api_key = os.getenv("GAODE_API_KEY", "")
gaode_nearby_url = os.getenv("GAODE_NEARBY_URL", "")
gaode_location_url = os.getenv("GAODE_LOCATION_URL", "")

# RAG 向量检索配置
embeddings_model_name = os.getenv("EMBEDDINGS_MODEL_NAME", "nomic-embed-text")
chroma_host = os.getenv("CHROMA_HOST", "localhost")
chroma_port = int(os.getenv("CHROMA_PORT", "8081"))
hf_home = os.getenv("HF_HOME", "")

# 数据库配置
connection_string = os.getenv("CONNECTION_STRING", "")