from langchain_openai import ChatOpenAI

from utils.env_util import qwen_model_name, qwen_api_key, qwen_base_url

qwen_llm = ChatOpenAI(
    model=qwen_model_name,
    api_key=qwen_api_key,
    base_url=qwen_base_url,
    temperature=0.3
)
