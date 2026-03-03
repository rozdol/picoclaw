from app.llm import chat_completion


SYSTEM_PROMPT = (
    "You are Coder, a pragmatic software engineering assistant. "
    "Return actionable code-focused guidance with short examples when needed."
)


async def run(user_text: str) -> str:
    return await chat_completion(SYSTEM_PROMPT, user_text)
