from collections.abc import Awaitable, Callable

from app.agents import coder, ops, researcher

AgentFn = Callable[[str], Awaitable[str]]

AGENT_MAP: dict[str, AgentFn] = {
    "researcher": researcher.run,
    "coder": coder.run,
    "ops": ops.run,
}


def available_agents() -> list[str]:
    return list(AGENT_MAP.keys())


def is_valid_agent(agent_name: str) -> bool:
    return agent_name in AGENT_MAP


async def run_agent(agent_name: str, user_text: str) -> str:
    if agent_name not in AGENT_MAP:
        raise ValueError(f"Unknown agent: {agent_name}")
    return await AGENT_MAP[agent_name](user_text)
