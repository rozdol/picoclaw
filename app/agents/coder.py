from app.llm import chat_completion


SYSTEM_PROMPT = (
    "You are Coder, a pragmatic software engineering assistant. "
    "Return actionable code-focused guidance with short examples when needed."
)


async def run(user_text: str, extra_system: str = "") -> str:
    system_prompt = SYSTEM_PROMPT
    if extra_system.strip():
        system_prompt = f"{SYSTEM_PROMPT}\n\n{extra_system.strip()}"
    return await chat_completion(system_prompt, user_text)
