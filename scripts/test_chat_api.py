import argparse
import codecs
import json
import sys
import uuid
import urllib.error
import urllib.request


SAMPLE_CSV = "类别,销售额\nA,10\nB,20\nA,5\n"


def request_json(
    url: str,
    method: str,
    body: bytes,
    headers: dict[str, str],
    timeout: int,
) -> tuple[int, dict | str]:
    req = urllib.request.Request(url=url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(text)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(text)
        except json.JSONDecodeError:
            return exc.code, text


def print_response(title: str, status: int, body: dict | str) -> None:
    print(f"\n[{title}] HTTP {status}")
    if isinstance(body, str):
        print(body)
    else:
        print(json.dumps(body, ensure_ascii=False, indent=2))


def upload_sample(base_url: str, timeout: int) -> str:
    boundary = f"----codex-{uuid.uuid4().hex}"
    multipart = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="sales.csv"\r\n'
        "Content-Type: text/csv\r\n"
        "\r\n"
        f"{SAMPLE_CSV}"
        f"\r\n--{boundary}--\r\n"
    ).encode("utf-8")

    status, body = request_json(
        url=f"{base_url}/api/upload",
        method="POST",
        body=multipart,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=timeout,
    )
    print_response("upload", status, body)

    if status != 200 or not isinstance(body, dict):
        raise RuntimeError("上传接口失败")

    return body["dataset_id"]


def chat(base_url: str, dataset_id: str, message: str, timeout: int) -> int:
    payload = json.dumps(
        {
            "dataset_id": dataset_id,
            "message": message,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    status, body = request_json(
        url=f"{base_url}/api/chat",
        method="POST",
        body=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    print_response("chat", status, body)

    return 0 if status == 200 else 1


def chat_stream(base_url: str, dataset_id: str, message: str, timeout: int) -> int:
    payload = json.dumps(
        {
            "dataset_id": dataset_id,
            "message": message,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url=f"{base_url}/api/chat/stream",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    print("\n[chat/stream]")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            print(f"HTTP {response.status}")
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            buffer = ""
            while True:
                chunk = response.read(256)
                if not chunk:
                    break
                buffer += decoder.decode(chunk)
                parts = buffer.split("\n\n")
                buffer = parts.pop() or ""
                for part in parts:
                    if not part.strip():
                        continue
                    print(part)
            buffer += decoder.decode(b"", final=True)
            if buffer.strip():
                print(buffer)
            return 0 if response.status == 200 else 1
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}")
        print(text)
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test app upload + chat API flow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--message", default="统计每个类别的销售额总和。")
    parser.add_argument("--stream", action="store_true", help="Test POST /api/chat/stream.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")

    dataset_id = args.dataset_id or upload_sample(base_url, args.timeout)
    if args.stream:
        return chat_stream(base_url, dataset_id, args.message, args.timeout)
    return chat(base_url, dataset_id, args.message, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
