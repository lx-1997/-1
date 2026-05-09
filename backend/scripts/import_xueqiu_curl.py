from __future__ import annotations

import argparse
import re
import shlex
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Xueqiu Cookie/User-Agent/Referer from a copied cURL command.")
    parser.add_argument("file", nargs="?", help="Optional file containing the copied cURL command. Reads stdin if omitted.")
    args = parser.parse_args()

    raw = Path(args.file).read_text() if args.file else sys.stdin.read()
    if not raw.strip():
        raise SystemExit("No cURL command supplied.")

    parsed = parse_curl(raw)
    if not parsed["cookie"]:
        raise SystemExit("No Cookie found. Copy the request as cURL from DevTools Network.")

    env = read_env()
    env["DEEPFOCUS_XUEQIU_COOKIE"] = parsed["cookie"]
    if parsed["user_agent"]:
        env["DEEPFOCUS_XUEQIU_USER_AGENT"] = parsed["user_agent"]
    if parsed["referer"]:
        env["DEEPFOCUS_XUEQIU_REFERER"] = parsed["referer"]
    elif parsed["url"]:
        env["DEEPFOCUS_XUEQIU_REFERER"] = "https://xueqiu.com/"
    if parsed["url"] and "/query/v1/search/status" in urlparse(parsed["url"]).path:
        env["DEEPFOCUS_XUEQIU_STATUS_URL"] = parsed["url"]
    write_env(env)

    print("Imported Xueqiu request credentials into backend/.env")
    print(f"cookie_length={len(parsed['cookie'])}")
    print(f"user_agent_configured={bool(parsed['user_agent'])}")
    print(f"referer_configured={bool(parsed['referer'])}")
    if parsed["url"]:
        path = urlparse(parsed["url"]).path
        print(f"copied_request_path={path}")
        if path.endswith("/search/user.json"):
            print("note=This copied request searches users, not discussions. For post content, copy the search/status request too.")


def parse_curl(raw: str) -> dict[str, str]:
    command = normalize_curl(raw)
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise SystemExit(f"Cannot parse cURL command: {exc}") from exc

    result = {"url": "", "cookie": "", "user_agent": "", "referer": ""}
    index = 0
    while index < len(parts):
        part = parts[index]
        if part.startswith("http") and not result["url"]:
            result["url"] = part
        elif part in {"-b", "--cookie"} and index + 1 < len(parts):
            result["cookie"] = parts[index + 1]
            index += 1
        elif part in {"-H", "--header"} and index + 1 < len(parts):
            header = parts[index + 1]
            name, _, value = header.partition(":")
            name = name.strip().lower()
            value = value.strip()
            if name == "cookie":
                result["cookie"] = value
            elif name == "user-agent":
                result["user_agent"] = value
            elif name == "referer":
                result["referer"] = value
            index += 1
        index += 1
    return result


def normalize_curl(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"\\\s*\n\s*", " ", text)
    return text


def read_env() -> dict[str, str]:
    source = ENV_PATH if ENV_PATH.exists() else ENV_EXAMPLE_PATH
    env: dict[str, str] = {}
    if source.exists():
        for line in source.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key] = value
    return env


def write_env(env: dict[str, str]) -> None:
    ordered_keys = [
        "DEEPFOCUS_LLM_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "MINIMAX_API_KEY",
        "MINIMAX_MODEL",
        "MINIMAX_BASE_URL",
        "CORS_ORIGINS",
        "FINNHUB_API_KEY",
        "ALPHAVANTAGE_API_KEY",
        "NASDAQ_EARNINGS_SCAN_DAYS",
        "EARNINGS_CACHE_TTL_SECONDS",
        "DEEPFOCUS_XUEQIU_COOKIE",
        "DEEPFOCUS_XUEQIU_USER_AGENT",
        "DEEPFOCUS_XUEQIU_REFERER",
        "DEEPFOCUS_XUEQIU_STATUS_URL",
    ]
    lines: list[str] = []
    seen: set[str] = set()
    for key in ordered_keys:
        if key in env:
            lines.append(f"{key}={quote_env(env[key])}")
            seen.add(key)
    for key in sorted(set(env) - seen):
        lines.append(f"{key}={quote_env(env[key])}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def quote_env(value: str) -> str:
    if not value:
        return ""
    if any(char in value for char in " #'\";"):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


if __name__ == "__main__":
    main()
