"""提示词模板。用户可在设置里选择或自定义。"""

from __future__ import annotations

TEMPLATES: dict[str, tuple[str, str]] = {
    "通用答题": (
        "通用答题",
        "请解答截图中的题目。选择题给出正确选项的字母；填空/简答题给出可以直接填入的答案。",
    ),
    "选择题（只判选项）": (
        "选择题",
        "这可能是选择题。请判断正确选项，answer 只填选项字母（如 B），"
        "并在 target 中给出该选项在截图中的坐标。",
    ),
    "简答/填空": (
        "简答填空",
        "这是填空或简答题。answer 只写需要填入的内容本身，不要带序号、不要解释，"
        "长度控制在 200 字以内。",
    ),
    "数学/计算": (
        "数学计算",
        "这是数学题。请在内部完成计算，answer 只输出最终结果（数值或最简表达式）。",
    ),
    "编程题": (
        "编程题",
        "这是编程题。answer 输出完整可提交的代码或函数，保持缩进正确，不要加 markdown 代码围栏。",
    ),
}

DEFAULT_TEMPLATE = "通用答题"


def prompt_for(template: str, custom: str) -> str:
    if template == "自定义":
        return custom.strip() or TEMPLATES[DEFAULT_TEMPLATE][1]
    return TEMPLATES.get(template, TEMPLATES[DEFAULT_TEMPLATE])[1]
