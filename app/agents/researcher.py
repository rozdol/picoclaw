from app.llm import chat_completion


SYSTEM_PROMPT = (
    "You are Researcher, a concise analysis assistant. "
    "Focus on factual, structured answers with explicit assumptions."
)


async def run(user_text: str) -> str:
    return await chat_completion(SYSTEM_PROMPT, user_text)
