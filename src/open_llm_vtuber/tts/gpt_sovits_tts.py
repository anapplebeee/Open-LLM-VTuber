"""GPT-SoVITS api_v2 TTS adapter.

GPT-SoVITS api_v2 calls ``GET /tts`` with the reference-audio fields on every
request. This adapter intentionally uses ``streaming_mode=0`` because OLV's
TTS interface returns one complete audio file; sentence-level streaming is
handled by OLV above this boundary.
"""

import re

import requests
from loguru import logger

from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    def __init__(
        self,
        api_url: str = "http://127.0.0.1:9880/tts",
        text_lang: str = "zh",
        ref_audio_path: str = "",
        prompt_lang: str = "zh",
        prompt_text: str = "",
        text_split_method: str = "cut5",
        batch_size: int = 1,
        speed_factor: float = 1.0,
        temperature: float = 1.0,
        media_type: str = "wav",
        streaming_mode: int = 0,
        timeout: int = 120,
        **_: object,
    ):
        self.api_url = api_url.rstrip("/")
        if not self.api_url.endswith("/tts"):
            self.api_url += "/tts"
        self.text_lang = text_lang
        self.ref_audio_path = ref_audio_path
        self.prompt_lang = prompt_lang
        self.prompt_text = prompt_text
        self.text_split_method = text_split_method
        self.batch_size = int(batch_size)
        self.speed_factor = max(0.5, min(2.0, float(speed_factor)))
        self.temperature = max(0.1, min(2.0, float(temperature)))
        self.media_type = media_type
        self.streaming_mode = int(streaming_mode)
        self.timeout = timeout

    def generate_audio(self, text: str, file_name_no_ext=None) -> str:
        if not self.ref_audio_path or not self.prompt_text:
            logger.error(
                "GPT-SoVITS api_v2 requires both ref_audio_path and prompt_text"
            )
            return None

        file_name = self.generate_cache_file_name(file_name_no_ext, self.media_type)
        params = {
            "text": re.sub(r"\[.*?\]", "", text),
            "text_lang": self.text_lang,
            "ref_audio_path": self.ref_audio_path,
            "prompt_lang": self.prompt_lang,
            "prompt_text": self.prompt_text,
            "text_split_method": self.text_split_method,
            "batch_size": self.batch_size,
            "speed_factor": self.speed_factor,
            "temperature": self.temperature,
            "media_type": self.media_type,
            "streaming_mode": 0,
        }

        try:
            response = requests.get(
                self.api_url,
                params=params,
                timeout=(10, self.timeout),
            )
            if response.status_code != 200:
                logger.error(
                    "GPT-SoVITS api_v2 returned {}: {}",
                    response.status_code,
                    response.text[:500],
                )
                return None
            if not response.content:
                logger.error("GPT-SoVITS api_v2 returned empty audio")
                return None
            with open(file_name, "wb") as audio_file:
                audio_file.write(response.content)
        except requests.RequestException as exc:
            logger.error(
                "GPT-SoVITS api_v2 request failed ({}): {}",
                self.api_url,
                type(exc).__name__,
            )
            return None
        return file_name


# api_v2 reference fields:
# text_lang / ref_audio_path / prompt_lang / prompt_text
