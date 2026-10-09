from .contracts import CanonicalTool, LLMCapabilities, LLMProvider, SpeechToTextProvider, TextToSpeechProvider
from .errors import ProviderError
from .fakes import FakeLLM, FakeSTT, FakeTTS
from .llm import GeminiLLM, OpenAICompatibleLLM, RoutedLLM, _gemini_text, _openai_text, stream_chat, stream_provider
from .stt import SherpaSpeechToText
from .tts import ElevenLabsTextToSpeech

llm_provider: LLMProvider = RoutedLLM()
stt_provider: SpeechToTextProvider = SherpaSpeechToText()
tts_provider: TextToSpeechProvider = ElevenLabsTextToSpeech()

__all__ = [
    "CanonicalTool",
    "FakeLLM",
    "FakeSTT",
    "FakeTTS",
    "GeminiLLM",
    "LLMCapabilities",
    "LLMProvider",
    "OpenAICompatibleLLM",
    "ElevenLabsTextToSpeech",
    "ProviderError",
    "RoutedLLM",
    "SherpaSpeechToText",
    "SpeechToTextProvider",
    "TextToSpeechProvider",
    "_gemini_text",
    "_openai_text",
    "llm_provider",
    "stt_provider",
    "stream_chat",
    "stream_provider",
    "tts_provider",
]
