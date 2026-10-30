import unittest

from src.open_llm_vtuber.agent.stateless_llm.openai_compatible_llm import (
    _is_loopback_url,
)


class OpenAICompatibleLLMTest(unittest.TestCase):
    def test_loopback_urls_are_detected(self):
        self.assertTrue(_is_loopback_url("http://127.0.0.1:7863/v1"))
        self.assertTrue(_is_loopback_url("http://localhost:11434/v1"))
        self.assertTrue(_is_loopback_url("http://[::1]:8080/v1"))

    def test_remote_urls_keep_default_proxy_behavior(self):
        self.assertFalse(_is_loopback_url("https://api.deepseek.com/v1"))
        self.assertFalse(_is_loopback_url("https://api.openai.com/v1"))


if __name__ == "__main__":
    unittest.main()
