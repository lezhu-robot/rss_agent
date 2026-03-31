import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

# 初始化两个 LLM (按能力分级)
api_key = os.getenv("OPENAI_API_KEY")
api_base = os.getenv("OPENAI_API_BASE")

fast_model_name = os.getenv("LLM_FAST_MODEL")
reasoning_model_name = os.getenv("LLM_REASONING_MODEL")

print(f"⚡️ Init Fast LLM: {fast_model_name}")
llm_fast = ChatOpenAI(
    model=fast_model_name,
    openai_api_key=api_key,
    openai_api_base=api_base,
    temperature=0.1 # Router 需要精准
)

print(f"🧠 Init Reasoning LLM: {reasoning_model_name}")
llm_reasoning = ChatOpenAI(
    model=reasoning_model_name,
    openai_api_key=api_key,
    openai_api_base=api_base,
    temperature=0.7 # Writer 需要创意
)

def get_bot_response(user_input: str) -> str:
    """
    核心函数：接收用户文本 -> 调用大模型 -> 返回回复
    """
    try:
        # Note: The original function used 'llm'.
        # You might need to update this to use 'router_llm' or 'writer_llm'
        # depending on your application logic.
        response = llm_fast.invoke(user_input)
        return response.content
    except Exception as e:
        return f"Sorry, AI brain error: {str(e)}"

def test_bot():
    print("🤖 Sending request to:", llm_fast.model_name)
    print("✅ Response:", get_bot_response("Hello!"))

if __name__ == "__main__":
    test_bot()
