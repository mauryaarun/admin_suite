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

    @staticmethod
    def normalize_url(url: str, default: str = "http://127.0.0.1:11434") -> str:
        if not url or not url.strip():
            return default
        u = url.strip()
        if not u.startswith("http://") and not u.startswith("https://"):
            u = f"http://{u}"
        return u.rstrip("/")

    def _set_default_base_url(self, base_url: Optional[str] = None) -> None:
        if base_url:
            self.base_url = self.normalize_url(base_url)
        elif self.provider == "ollama":
            self.base_url = "http://127.0.0.1:11434"
        elif self.provider in ("openai", "openrouter", "groq", "deepseek"):
            self.base_url = "https://api.openai.com/v1"
        elif self.provider == "anthropic":
            self.base_url = "https://api.anthropic.com/v1"
        elif self.provider == "gemini":
            self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        else:
            self.base_url = self.normalize_url(base_url) if base_url else "http://127.0.0.1:11434"

    def _get_candidate_urls(self) -> list[str]:
        """Return candidate URLs for Ollama fallback between localhost and 127.0.0.1."""
        if self.provider != "ollama":
            return [self.base_url]
        primary = self.base_url
        candidates = [primary]
        if "127.0.0.1" in primary:
            alt = primary.replace("127.0.0.1", "localhost")
            if alt not in candidates:
                candidates.append(alt)
        elif "localhost" in primary:
            alt = primary.replace("localhost", "127.0.0.1")
            if alt not in candidates:
                candidates.append(alt)
        return candidates

    def _open_url(self, req: urllib.request.Request, timeout: float = 10):
        """Open a request, explicitly bypassing system HTTP proxies for local addresses."""
        url = req.full_url.lower()
        if "127.0.0.1" in url or "localhost" in url:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            return opener.open(req, timeout=timeout)
        return urllib.request.urlopen(req, timeout=timeout)

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
                for candidate in self._get_candidate_urls():
                    for path in ("/api/version", "/api/tags", "/"):
                        try:
                            req = urllib.request.Request(f"{candidate}{path}")
                            req.add_header("User-Agent", "AdminSuite-Copilot/1.0")
                            with self._open_url(req, timeout=6) as resp:
                                if resp.status == 200:
                                    self.base_url = candidate
                                    return True
                        except Exception:
                            continue
                return False

            elif self.provider in ("openai", "openrouter", "groq", "deepseek"):
                headers = {"Content-Type": "application/json", "User-Agent": "AdminSuite-Copilot/1.0"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                req = urllib.request.Request(f"{self.base_url}/models", headers=headers)
                with self._open_url(req, timeout=6) as resp:
                    return resp.status == 200

            elif self.provider == "anthropic":
                return bool(self.api_key)

            return True
        except Exception as exc:
            logger.debug(f"LLM ping failed for provider '{self.provider}': {exc}")
            return False

    def test_connection_detailed(self) -> dict[str, Any]:
        """Run thorough diagnostics on the current endpoint and return results."""
        if self.provider == "ollama":
            tested_urls = []
            last_err = ""
            for candidate in self._get_candidate_urls():
                tested_urls.append(candidate)
                try:
                    req_ver = urllib.request.Request(f"{candidate}/api/version")
                    req_ver.add_header("User-Agent", "AdminSuite-Copilot/1.0")
                    version_str = "ready"
                    try:
                        with self._open_url(req_ver, timeout=5) as resp:
                            if resp.status == 200:
                                v_data = json.loads(resp.read().decode("utf-8"))
                                version_str = v_data.get("version", "ready")
                    except Exception:
                        pass

                    req_tags = urllib.request.Request(f"{candidate}/api/tags")
                    req_tags.add_header("User-Agent", "AdminSuite-Copilot/1.0")
                    with self._open_url(req_tags, timeout=8) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                            models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                            self.base_url = candidate
                            return {
                                "ok": True,
                                "provider": "ollama",
                                "url": candidate,
                                "version": version_str,
                                "models": models,
                                "message": f"Connected to Ollama ({version_str}) at {candidate}. Found {len(models)} installed model(s).",
                            }
                except Exception as e:
                    last_err = str(e)
                    continue

            return {
                "ok": False,
                "provider": "ollama",
                "url": self.base_url,
                "error": last_err or "Connection failed",
                "message": (
                    f"Could not connect to Ollama at {', '.join(tested_urls)}.\n"
                    f"Details: {last_err}\n\n"
                    f"Troubleshooting tips:\n"
                    f"• Ensure Ollama is running (`ollama serve` or `systemctl status ollama`).\n"
                    f"• Try `curl -s http://127.0.0.1:11434/api/tags` in your terminal.\n"
                    f"• Check firewall / port 11434 settings if Ollama is remote or in Docker."
                ),
            }
        else:
            ok = self.ping()
            return {
                "ok": ok,
                "provider": self.provider,
                "url": self.base_url,
                "message": f"Connection to {self.provider} endpoint {'succeeded' if ok else 'failed (check API key / endpoint)'}."
            }

    # ------------------------------------------------------------
    # List Available Models
    # ------------------------------------------------------------
    def list_models(self) -> list[dict[str, Any]]:
        """Return list of available models [{name, ...}]."""
        if self.provider == "ollama":
            for candidate in self._get_candidate_urls():
                url = f"{candidate}/api/tags"
                req = urllib.request.Request(url)
                req.add_header("User-Agent", "AdminSuite-Copilot/1.0")
                try:
                    with self._open_url(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        self.base_url = candidate
                        return data.get("models", [])
                except Exception as e:
                    logger.debug(f"Failed to fetch Ollama models from {candidate}: {e}")
            return []

        elif self.provider in ("openai", "openrouter", "groq", "deepseek"):
            if not self.api_key and not self.base_url.startswith("http://localhost"):
                return [{"name": "gpt-4o-mini"}, {"name": "gpt-4o"}, {"name": "o3-mini"}]
            try:
                headers = {"Content-Type": "application/json", "User-Agent": "AdminSuite-Copilot/1.0"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                req = urllib.request.Request(f"{self.base_url}/models", headers=headers)
                with self._open_url(req, timeout=6) as resp:
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
        payload = {"model": model, "messages": messages, "stream": stream}
        data_bytes = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "AdminSuite-Copilot/1.0"}

        last_exc = None
        for candidate in self._get_candidate_urls():
            url = f"{candidate}/api/chat"
            req = urllib.request.Request(url, data=data_bytes, headers=headers)
            try:
                if stream:
                    return self._stream_ollama(req, candidate=candidate)
                with self._open_url(req, timeout=120) as resp:
                    self.base_url = candidate
                    data = json.loads(resp.read().decode("utf-8"))
                    return {"message": {"content": data.get("message", {}).get("content", "")}}
            except urllib.error.HTTPError as he:
                err_body = ""
                try:
                    raw_err = he.read().decode("utf-8", errors="replace")
                    err_body = json.loads(raw_err).get("error", raw_err)
                except Exception:
                    pass
                raise RuntimeError(f"Ollama error (HTTP {he.code}): {err_body or he.reason}")
            except Exception as exc:
                last_exc = exc
                continue

        if last_exc:
            raise RuntimeError(f"Failed to connect to Ollama: {last_exc}")

    def _stream_ollama(self, req: urllib.request.Request, candidate: Optional[str] = None) -> Generator[dict, None, None]:
        try:
            resp = self._open_url(req, timeout=180)
            if candidate:
                self.base_url = candidate
        except urllib.error.HTTPError as he:
            err_body = ""
            try:
                raw_err = he.read().decode("utf-8", errors="replace")
                err_body = json.loads(raw_err).get("error", raw_err)
            except Exception:
                pass
            raise RuntimeError(f"Ollama error (HTTP {he.code}): {err_body or he.reason}")
        except Exception as exc:
            raise RuntimeError(f"Failed to connect to Ollama at {self.base_url}: {exc}")

        with resp:
            for line in resp:
                if line:
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        yield chunk
                    except Exception:
                        continue

    def pull_model(self, model_name: str) -> Generator[dict, None, None]:
        """Stream pull progress for an Ollama model."""
        if self.provider != "ollama":
            raise ValueError("Pull model is only supported for Ollama")
        url = f"{self.base_url}/api/pull"
        payload = {"name": model_name, "stream": True}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "AdminSuite-Copilot/1.0"},
        )
        with self._open_url(req, timeout=600) as resp:
            for line in resp:
                if line:
                    try:
                        yield json.loads(line.decode("utf-8"))
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
