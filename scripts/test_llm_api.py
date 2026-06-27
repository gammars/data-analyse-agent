import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from getpass import getpass

from dotenv import load_dotenv


def mask_secret(value: str | None) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def build_url(base_url: str, path: str) -> str:
    return f"{normalize_base_url(base_url)}/{path.lstrip('/')}"


def request_json(
    url: str,
    api_key: str,
    method: str = "POST",
    payload: dict | None = None,
    timeout: int = 30,
) -> tuple[int, dict | list | str]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed_body = json.loads(body)
        except json.JSONDecodeError:
            parsed_body = body
        return exc.code, parsed_body


def print_json(data: dict | list | str) -> None:
    if isinstance(data, str):
        print(data)
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


def test_chat(base_url: str, api_key: str, model: str, timeout: int) -> int:
    url = build_url(base_url, "/chat/completions")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个用于接口连通性测试的助手。"},
            {"role": "user", "content": "请只回复：API OK"},
        ],
        "temperature": 0,
        "stream": False,
    }

    print(f"\n[chat] POST {url}")
    started_at = time.perf_counter()
    status, body = request_json(url, api_key, payload=payload, timeout=timeout)
    elapsed = time.perf_counter() - started_at

    print(f"[chat] HTTP {status}, elapsed={elapsed:.2f}s")
    print_json(body)

    if status != 200:
        return 1

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print("[chat] 响应格式异常：没有 choices[0].message.content")
        return 1

    print(f"\n[chat] 模型回复：{content}")
    return 0


def test_models(base_url: str, api_key: str, timeout: int) -> int:
    url = build_url(base_url, "/models")

    print(f"\n[models] GET {url}")
    started_at = time.perf_counter()
    status, body = request_json(url, api_key, method="GET", timeout=timeout)
    elapsed = time.perf_counter() - started_at

    print(f"[models] HTTP {status}, elapsed={elapsed:.2f}s")
    print_json(body)

    return 0 if status == 200 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test OpenAI-compatible LLM API connectivity.")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL.")
    parser.add_argument("--api-key", default=None, help="API key. If omitted, reads OPENAI_API_KEY.")
    parser.add_argument("--model", default=None, help="Model name. If omitted, reads OPENAI_MODEL.")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout seconds.")
    parser.add_argument("--list-models", action="store_true", help="Also test GET /models.")
    parser.add_argument("--skip-chat", action="store_true", help="Only run non-chat checks.")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    base_url = args.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    model = args.model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"

    if not api_key:
        api_key = getpass("OPENAI_API_KEY is empty, input API key: ").strip()

    if not api_key:
        print("ERROR: OPENAI_API_KEY 为空。请在 .env 中配置，或通过 --api-key 传入。")
        return 1

    print("LLM API config")
    print(f"- OPENAI_BASE_URL: {base_url}")
    print(f"- OPENAI_MODEL: {model}")
    print(f"- OPENAI_API_KEY: {mask_secret(api_key)}")

    exit_code = 0
    if args.list_models:
        exit_code |= test_models(base_url, api_key, args.timeout)

    if not args.skip_chat:
        exit_code |= test_chat(base_url, api_key, model, args.timeout)

    if exit_code == 0:
        print("\nOK: API 连通性测试通过。")
    else:
        print("\nFAILED: API 测试失败，请查看上方 HTTP 状态码和错误体。")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
