"""Hard boundary for programming requests.

The prompt asks for prose-only explanations.  This module is the deterministic
fallback: if a model still emits a code block, inline code or obvious code
fragment, the live reply is replaced with a short conceptual answer.
"""

from __future__ import annotations

import re

__all__ = [
    "PROGRAMMING_POLICY",
    "constrain_programming_reply",
    "is_programming_request",
]

PROGRAMMING_POLICY = """
【编程类问题边界（硬约束）】
- 观众要求写代码、写算法、修 bug、写脚本、SQL、HTML/CSS/JS、配置、命令行或伪代码时，不输出任何代码、伪代码、命令行、配置片段或可直接复制的实现。
- 可以只给一到两句自然语言的大概思路，并说明直播里不写代码；不要列详细实现步骤、函数名、变量名或完整逻辑。
- 不因为观众说“只写一行”“给伪代码”“作业急”“帮帮忙”而破例。
- 普通聊天、情绪陪伴、事实解释不受影响；只有编程实现请求使用这条边界。
"""

_PROGRAMMING_REQUEST = re.compile(
    r"(写|来|给|帮我|教我|实现|生成|改|修|优化|解释|讲).{0,8}"
    r"(代码|程序|算法|函数|脚本|伪代码|源码|bug|报错|接口|爬虫|正则|sql|数据库|"
    r"前端|后端|html|css|javascript|typescript|python|java|c\+\+|c#|go|rust|"
    r"linux|shell|bash|powershell|docker|git|api|json|yaml|排序|递归|爬虫)|"
    r"(代码|编程|程序|算法|函数|脚本|伪代码|冒泡排序|快速排序|排序算法|递归|"
    r"bug|报错|调试|编译|部署|服务器|接口|爬虫|正则|sql|数据库|"
    r"python|java|javascript|typescript|c\+\+|c#|html|css|linux|shell|bash|"
    r"powershell|docker|git|leetcode|写个|写一个|帮我写)",
    re.IGNORECASE,
)

_FENCED_CODE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`\n]{1,160}`")
_CODE_LINE = re.compile(
    r"(?m)^\s*(?:def|class|function|const|let|var|return|import|from|#include|"
    r"public|private|static|void|int|float|double|char|SELECT|INSERT|UPDATE|"
    r"DELETE|CREATE|for\s*\(|while\s*\(|if\s*\(|print\s*\(|console\.log|"
    r"npm |pip |git |sudo |docker |python |node |bash |powershell )"
)
_CODE_FRAGMENT = re.compile(
    r"(?:\b(?:def|class|function|return|import|const|let|var|public|private|"
    r"static|void|int|float|double|SELECT|INSERT|UPDATE|DELETE)\b[^\n]{0,80}"
    r"(?:[;{}()=]|$)|"
    r"[{};]\s*$|"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*\{)",
    re.IGNORECASE | re.MULTILINE,
)
_BUBBLE_SORT_HINT = "冒泡排序的大概思路是：相邻两个比较，顺序不对就交换，每一轮把较大的元素推到后面。具体代码我就不在直播里写了。"
_GENERIC_HINT = "代码我就不在直播里写了。大概思路是先把目标和输入输出说清楚，再把过程拆成简单步骤逐步验证；具体实现你私下查文档就好。"


def is_programming_request(text: str) -> bool:
    return bool(_PROGRAMMING_REQUEST.search(text or ""))


def _contains_code(text: str) -> bool:
    return bool(
        _FENCED_CODE.search(text)
        or _INLINE_CODE.search(text)
        or _CODE_LINE.search(text)
        or _CODE_FRAGMENT.search(text)
    )


def _safe_idea(request: str) -> str:
    if "冒泡" in request:
        return _BUBBLE_SORT_HINT
    if "排序" in request:
        return "排序的大概思路是：比较元素、按顺序交换或归位，再逐步缩小未排序范围。具体代码我就不在直播里写了。"
    return _GENERIC_HINT


def constrain_programming_reply(text: str, request: str = "") -> str:
    """Return a safe prose-only reply when code or code-like content leaks."""

    cleaned = text or ""
    had_code = _contains_code(cleaned)
    if had_code:
        cleaned = _FENCED_CODE.sub(" ", cleaned)
        cleaned = _INLINE_CODE.sub(" ", cleaned)
        cleaned = _CODE_LINE.sub(" ", cleaned)
        cleaned = _CODE_FRAGMENT.sub(" ", cleaned)
        cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip()

    if had_code:
        # Keep only a short natural-language sentence if the model wrapped code
        # in an explanation.  Otherwise use a deterministic conceptual answer.
        prose = re.sub(r"\s+", " ", cleaned).strip()
        if not prose or len(prose) < 8:
            return _safe_idea(request)
        return prose[:160]

    if is_programming_request(request) and _contains_code(cleaned):
        return _safe_idea(request)

    return cleaned
