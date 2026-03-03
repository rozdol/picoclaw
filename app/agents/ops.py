from app.llm import chat_completion


SYSTEM_PROMPT = (
    "You are Ops, an infrastructure and operations assistant. "
    "Prioritize safe, reversible steps with explicit commands and checks."
)


async def run(user_text: str) -> str:
    return await chat_completion(SYSTEM_PROMPT, user_text)
