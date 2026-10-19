"""
Universal AI LLM Client for Admin Suite.
Provides context-aware completions using standard library urllib without third-party dependencies.
Supports:
  - Local Ollama (http://127.0.0.1:11434)
  - OpenAI & OpenAI-Compatible Gateways (OpenAI, DeepSeek, Groq, OpenRouter, vLLM, LocalAI)
  - Anthropic Claude API
  - Google Gemini API
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)


class UniversalLlmClient:
    """
    Universal multi-provider LLM client with streaming support and zero extra dependencies.
    """

    PROVIDERS = ["ollama", "openai", "anthropic", "gemini"]

    def __init__(
        self,
        provider: str = "ollama",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = provider.lower()
        self.api_key = api_key or ""
        self._set_default_base_url(base_url)

    def _set_default_base_url(self, base_url: Optional[str] = None) -> None:
        if base_url:
            self.base_url = base_url.rstrip("/")
        elif self.provider == "ollama":
            self.base_url = "http://127.0.0.1:11434"
        elif self.provider in ("openai", "openrouter", "groq", "deepseek"):
            self.base_url = "https://api.openai.com/v1"
        elif self.provider == "anthropic":
            self.base_url = "https://api.anthropic.com/v1"
        elif self.provider == "gemini":
            self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        else:
            self.base_url = base_url.rstrip("/") if base_url else "http://127.0.0.1:11434"

    def configure(
        self,
        provider: str,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.provider = provider.lower()
        if api_key is not None:
            self.api_key = api_key
        self._set_default_base_url(base_url)

    # ------------------------------------------------------------
    # Health check / Ping
    # ------------------------------------------------------------
    def ping(self) -> bool:
        """Test if the configured AI endpoint is reachable."""
        try:
            if self.provider == "ollama":
                req = urllib.request.Request(f"{self.base_url}/api/tags")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    return resp.status == 200

            elif self.provider in ("openai", "openrouter", "groq", "deepseek"):
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                req = urllib.request.Request(f"{self.base_url}/models", headers=headers)
                with urllib.request.urlopen(req, timeout=4) as resp:
                    return resp.status == 200

            elif self.provider == "anthropic":
                return bool(self.api_key)

            return True
        except Exception as exc:
            logger.debug(f"LLM ping failed for provider '{self.provider}': {exc}")
            return False

    # ------------------------------------------------------------
    # List Available Models
    # ------------------------------------------------------------
    def list_models(self) -> list[dict[str, Any]]:
        """Return list of available models [{name, ...}]."""
        if self.provider == "ollama":
            url = f"{self.base_url}/api/tags"
            req = urllib.request.Request(url)
            try:
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("models", [])
            except Exception as e:
                logger.debug(f"Failed to fetch Ollama models: {e}")
                return []

        elif self.provider in ("openai", "openrouter", "groq", "deepseek"):
            if not self.api_key and not self.base_url.startswith("http://localhost"):
                return [{"name": "gpt-4o-mini"}, {"name": "gpt-4o"}, {"name": "o3-mini"}]
            try:
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                req = urllib.request.Request(f"{self.base_url}/models", headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = data.get("data", [])
                    return [{"name": m.get("id", m.get("name", "unknown"))} for m in models]
            except Exception as exc:
                logger.debug(f"Failed to fetch OpenAI models: {exc}")
                return [{"name": "gpt-4o-mini"}, {"name": "gpt-4o"}, {"name": "o3-mini"}]

        elif self.provider == "anthropic":
            return [
                {"name": "claude-3-5-sonnet-latest"},
                {"name": "claude-3-5-haiku-latest"},
                {"name": "claude-3-opus-latest"},
            ]

        elif self.provider == "gemini":
            return [
                {"name": "gemini-1.5-flash"},
                {"name": "gemini-1.5-pro"},
                {"name": "gemini-2.0-flash"},
            ]

        return [{"name": "default"}]

    # ------------------------------------------------------------
    # Chat Completion (Streaming & Non-Streaming)
    # ------------------------------------------------------------
    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool = False,
    ) -> Any:
        if self.provider == "ollama":
            return self._chat_ollama(model, messages, stream)
        elif self.provider in ("openai", "openrouter", "groq", "deepseek"):
            return self._chat_openai(model, messages, stream)
        elif self.provider == "anthropic":
            return self._chat_anthropic(model, messages, stream)
        elif self.provider == "gemini":
            return self._chat_gemini(model, messages, stream)
        else:
            return self._chat_openai(model, messages, stream)

    # ------------------------------------------------------------
    # Provider Implementations
    # ------------------------------------------------------------
    def _chat_ollama(self, model: str, messages: list[dict[str, str]], stream: bool):
        url = f"{self.base_url}/api/chat"
        payload = {"model": model, "messages": messages, "stream": stream}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        if stream:
            return self._stream_ollama(req)
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"message": {"content": data.get("message", {}).get("content", "")}}

    def _stream_ollama(self, req: urllib.request.Request) -> Generator[dict, None, None]:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for line in resp:
                if line:
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        yield chunk
                    except Exception:
                        continue

    def _chat_openai(self, model: str, messages: list[dict[str, str]], stream: bool):
        url = f"{self.base_url}/chat/completions"
        payload = {"model": model, "messages": messages, "stream": stream}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        if stream:
            return self._stream_openai(req)

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            return {"message": {"content": content}}

    def _stream_openai(self, req: urllib.request.Request) -> Generator[dict, None, None]:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for line in resp:
                decoded = line.decode("utf-8").strip()
                if not decoded or not decoded.startswith("data:"):
                    continue
                data_part = decoded[5:].strip()
                if data_part == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_part)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield {"message": {"content": token}}
                except Exception:
                    continue

    def _chat_anthropic(self, model: str, messages: list[dict[str, str]], stream: bool):
        url = f"{self.base_url}/messages"
        # Convert system message if present
        system_prompt = ""
        user_msgs = []
        for m in messages:
            if m.get("role") == "system":
                system_prompt += m.get("content", "") + "\n"
            else:
                user_msgs.append(m)

        payload: dict[str, Any] = {
            "model": model or "claude-3-5-sonnet-latest",
            "messages": user_msgs,
            "max_tokens": 4096,
            "stream": stream,
        }
        if system_prompt:
            payload["system"] = system_prompt.strip()

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)

        if stream:
            return self._stream_anthropic(req)

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content_blocks = data.get("content", [])
            text = "".join(b.get("text", "") for b in content_blocks)
            return {"message": {"content": text}}

    def _stream_anthropic(self, req: urllib.request.Request) -> Generator[dict, None, None]:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for line in resp:
                decoded = line.decode("utf-8").strip()
                if not decoded.startswith("data:"):
                    continue
                data_part = decoded[5:].strip()
                try:
                    chunk = json.loads(data_part)
                    if chunk.get("type") == "content_block_delta":
                        token = chunk.get("delta", {}).get("text", "")
                        if token:
                            yield {"message": {"content": token}}
                except Exception:
                    continue

    def _chat_gemini(self, model: str, messages: list[dict[str, str]], stream: bool):
        # Maps chat messages to Gemini generateContent format
        clean_model = model or "gemini-1.5-flash"
        url = f"{self.base_url}/models/{clean_model}:generateContent?key={self.api_key}"
        contents = []
        for m in messages:
            role = "user" if m.get("role") != "assistant" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": m.get("content", "")}],
            })
        payload = {"contents": contents}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
            text = "".join(p.get("text", "") for p in parts)
            return {"message": {"content": text}}
