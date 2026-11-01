from __future__ import annotations

import unittest

from src.open_llm_vtuber.live.programming_guard import (
    constrain_programming_reply,
    is_programming_request,
)


class ProgrammingGuardTest(unittest.TestCase):
    def test_detects_programming_requests(self):
        self.assertTrue(is_programming_request("弹幕让我写一个冒泡排序"))
        self.assertTrue(is_programming_request("帮我写个 Python 脚本"))
        self.assertFalse(is_programming_request("今天直播好开心"))

    def test_removes_fenced_code_and_keeps_prose(self):
        reply = """可以这样想：相邻元素两两比较，顺序不对就交换。
```python
def bubble_sort(arr):
    for i in range(len(arr)):
        for j in range(len(arr) - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
```
"""
        safe = constrain_programming_reply(reply, "写个冒泡排序")
        self.assertNotIn("```", safe)
        self.assertNotIn("def bubble_sort", safe)
        self.assertIn("相邻元素", safe)

    def test_code_only_reply_gets_conceptual_fallback(self):
        safe = constrain_programming_reply(
            "```python\nprint('hello')\n```", "写个冒泡排序"
        )
        self.assertIn("冒泡排序", safe)
        self.assertNotIn("print(", safe)
        self.assertNotIn("```", safe)

    def test_inline_code_is_removed(self):
        safe = constrain_programming_reply(
            "大概是用 `arr[j]` 比较。", "排序怎么写"
        )
        self.assertNotIn("`", safe)
        self.assertIn("比较", safe)

    def test_non_programming_reply_is_unchanged(self):
        text = "今天辛苦了，早点休息。"
        self.assertEqual(constrain_programming_reply(text, "今天好累"), text)


if __name__ == "__main__":
    unittest.main()
