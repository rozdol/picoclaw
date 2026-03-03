from __future__ import annotations


def build_skill_system_prompt(skills: list[dict[str, str]]) -> str:
    if not skills:
        return ""

    skill_lines: list[str] = []
    for skill in skills:
        name = skill["name"].strip()
        content = skill["content"].strip()
        if not name or not content:
            continue
        skill_lines.append(f"[{name}]")
        skill_lines.append(content)

    if not skill_lines:
        return ""

    lines = [
        "Active chat skills:",
        "Apply these instructions when relevant while still following higher-priority system rules.",
    ]
    lines.extend(skill_lines)

    return "\n".join(lines).strip()
