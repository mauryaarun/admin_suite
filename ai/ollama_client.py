"""
Lightweight Ollama API client using urllib (no extra dependencies).
"""
import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

class OllamaClient:
    def __init__(self, base_url="http://127.0.0.1:11434"):
        self.base_url = self.normalize_url(base_url)
        logger.info(f"OllamaClient initialized with base_url={self.base_url}")

    @staticmethod
    def normalize_url(url: str, default: str = "http://127.0.0.1:11434") -> str:
        if not url or not url.strip():
            return default
        u = url.strip()
        if not u.startswith("http://") and not u.startswith("https://"):
            u = f"http://{u}"
        return u.rstrip("/")

    def _get_candidate_urls(self) -> list[str]:
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
        url = req.full_url.lower()
        if "127.0.0.1" in url or "localhost" in url:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            return opener.open(req, timeout=timeout)
        return urllib.request.urlopen(req, timeout=timeout)

    def list_models(self) -> list[dict]:
        """Return list of installed models [{name, size, ...}]."""
        last_err = None
        for candidate in self._get_candidate_urls():
            url = f"{candidate}/api/tags"
            logger.info(f"Fetching model list from {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "AdminSuite-OllamaClient/1.0"})
            try:
                with self._open_url(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = data.get("models", [])
                    self.base_url = candidate
                    logger.info(f"Successfully fetched {len(models)} model(s)")
                    return models
            except Exception as e:
                last_err = e
                continue
        raise ConnectionError(f"Failed to connect to Ollama at {self.base_url}: {last_err}")

    def ping(self) -> bool:
        """Test if Ollama is reachable."""
        for candidate in self._get_candidate_urls():
            for path in ("/api/version", "/api/tags", "/"):
                url = f"{candidate}{path}"
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "AdminSuite-OllamaClient/1.0"})
                    with self._open_url(req, timeout=5) as resp:
                        if resp.status == 200:
                            self.base_url = candidate
                            logger.info(f"Ollama ping successful at {url}")
                            return True
                except Exception:
                    continue
        return False

    def chat(self, model, messages, stream=False):
        url = f"{self.base_url}/api/chat"
        logger.info(f"Sending chat request to {url} with model={model}, stream={stream}")
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            if stream:
                logger.info("Using streaming response")
                return self._stream_response(req)
            else:
                logger.info("Using non-streaming response")
                with self._open_url(req, timeout=120) as response:
                    body = response.read().decode("utf-8")
                    logger.info(f"Received response ({len(body)} bytes)")
                    return json.loads(body)
        except urllib.error.URLError as e:
            logger.error(f"Failed to connect to Ollama at {self.base_url}: {e}")
            raise ConnectionError(f"Failed to connect to Ollama at {self.base_url}: {e}")
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            raise RuntimeError(f"Ollama request failed: {e}")

    def _stream_response(self, req):
        logger.info("Starting streaming response")
        try:
            with self._open_url(req, timeout=180) as response:
                for line in response:
                    if line:
                        try:
                            chunk = json.loads(line.decode("utf-8"))
                            yield chunk
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to decode streaming chunk: {e}")
                            continue
        except Exception as e:
            logger.error(f"Error during streaming: {e}")
            raise