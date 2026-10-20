#!/usr/bin/env python3
"""relay-imagegen: generate / edit images through an OpenAI-compatible relay.

Only uses the Python standard library, so it works with a bare `py -3` install.
Routes:
  images     -> POST {base}/images/generations   (most relay "image groups")
  responses  -> POST {base}/responses with the hosted image_generation tool
`auto` tries images first, then responses.

Config file (KEY=VALUE lines, defaults to ~/.codex/imagegen/config.env):
  BASE_URL=        relay base url, usually ends with /v1
  API_KEY=         key that belongs to the relay's image group
  IMAGE_MODEL=     auto (default) or gpt-image-2 / gpt-image-2.5 / ...
  RESPONSES_MODEL= chat model used only by the responses route
  ROUTE=           auto (default) | images | responses
  OUT_DIR=         output directory
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# Keep Chinese diagnostics readable when the shell captures UTF-8 output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_IMAGE = 3
EXIT_HTTP = 4
EXIT_ALL_ROUTES = 5

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 521, 522, 524}
DEFAULT_CONFIG = Path(
    os.environ.get("IMAGEGEN_CONFIG")
    or (Path.home() / ".codex" / "imagegen" / "config.env")
)
DEFAULT_OUT_DIR = Path(
    os.environ.get("IMAGEGEN_OUT_DIR")
    or (Path.cwd() / "outputs" / "codex-images")
)
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class AppError(Exception):
    def __init__(self, message: str, code: int = EXIT_USAGE):
        super().__init__(message)
        self.code = code


def eprint(*args) -> None:
    print(*args, file=sys.stderr)


# --------------------------------------------------------------------------- config


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def normalize_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        raise AppError("BASE_URL 为空，请先填写 config.env")
    if not url.startswith(("http://", "https://")):
        raise AppError(f"BASE_URL 必须以 http:// 或 https:// 开头：{url}")
    parsed = urllib.parse.urlsplit(url)
    if not parsed.path or parsed.path == "/":
        url = url + "/v1"
    for suffix in (
        "/images/generations",
        "/images/edits",
        "/chat/completions",
        "/responses",
        "/models",
    ):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip("/")


class Config:
    def __init__(self, path: Path, args: argparse.Namespace):
        file_values = read_env_file(path)
        env = os.environ

        def pick(file_key: str, env_key: str, fallback: str = "") -> str:
            cli = getattr(args, file_key.lower().replace("_", ""), None)
            if isinstance(cli, str) and cli.strip():
                return cli.strip()
            if file_values.get(file_key.lower()):
                return file_values[file_key.lower()]
            if env.get(env_key):
                return env[env_key]
            return fallback

        self.config_path = path
        self.base_url = normalize_base_url(pick("BASE_URL", "IMAGEGEN_BASE_URL"))
        self.api_key = pick("API_KEY", "IMAGEGEN_API_KEY")
        if not self.api_key:
            raise AppError(
                f"没有找到 API_KEY。请在 {path} 里写一行 API_KEY=sk-... "
                "（或设置环境变量 IMAGEGEN_API_KEY）"
            )
        self.image_model = pick("IMAGE_MODEL", "IMAGEGEN_IMAGE_MODEL", "auto") or "auto"
        self.responses_model = pick("RESPONSES_MODEL", "IMAGEGEN_RESPONSES_MODEL")
        self.route = (pick("ROUTE", "IMAGEGEN_ROUTE", "auto") or "auto").lower()
        out_dir = pick("OUT_DIR", "IMAGEGEN_OUT_DIR")
        self.out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
        self.default_size = pick("SIZE", "IMAGEGEN_SIZE", "1024x1024")
        self.default_quality = pick("QUALITY", "IMAGEGEN_QUALITY", "medium")

    def masked_key(self) -> str:
        key = self.api_key
        if len(key) <= 12:
            return key[:2] + "***"
        return key[:6] + "..." + key[-4:]


# --------------------------------------------------------------------------- http


def _seconds(attempt: int) -> float:
    return min(2 ** attempt, 8)


def _safe_body(error: urllib.error.HTTPError) -> str:
    try:
        raw = error.read()
    except Exception:  # noqa: BLE001
        return ""
    text = raw.decode("utf-8", "replace")
    return text[:1500]


def http_open(
    method: str,
    url: str,
    api_key: str,
    payload: dict | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    accept: str | None = None,
    timeout: int = 300,
    retries: int = 2,
):
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "relay-imagegen/1.0"}
    data = body
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = content_type or "application/json"
    if accept:
        headers["Accept"] = accept
    attempt = 0
    while True:
        attempt += 1
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = _safe_body(exc)
            if exc.code in RETRYABLE_STATUS and attempt <= retries:
                eprint(f"[retry] HTTP {exc.code} from {url} (attempt {attempt}), retrying...")
                time.sleep(_seconds(attempt))
                continue
            raise AppError(
                f"HTTP {exc.code} 调用 {url} 失败：{detail or exc.reason}", EXIT_HTTP
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt <= retries:
                eprint(f"[retry] 网络错误 {exc} (attempt {attempt}), retrying...")
                time.sleep(_seconds(attempt))
                continue
            raise AppError(f"网络错误，调用 {url} 失败：{exc}", EXIT_HTTP) from exc


def get_models(cfg: Config, timeout: int = 30) -> list[str]:
    response = http_open("GET", f"{cfg.base_url}/models", cfg.api_key, timeout=timeout, retries=1)
    with response:
        raw = response.read()
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise AppError(f"{cfg.base_url}/models 返回的不是 JSON：{raw[:200]!r}", EXIT_HTTP) from exc
    entries = data.get("data") if isinstance(data, dict) else data
    models: list[str] = []
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("id"):
            models.append(str(entry["id"]))
        elif isinstance(entry, str):
            models.append(entry)
    return models


def pick_image_model(models: list[str]) -> str:
    candidates = []
    for name in models:
        match = re.search(r"gpt-image-([0-9][0-9.]*)", name, re.IGNORECASE)
        if match and "flare" not in name and "sunburst" not in name:
            candidates.append((match.group(1), name))
    if candidates:
        candidates.sort(key=lambda item: [int(p) if p.isdigit() else 0 for p in item[0].split(".")])
        return candidates[-1][1]
    for name in models:
        if re.search(r"gpt-image|image", name, re.IGNORECASE):
            return name
    return "gpt-image-2"


def read_codex_model() -> str:
    """Top-level `model = "..."` from ~/.codex/config.toml (best effort, no TOML parser)."""
    path = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "config.toml"
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            break
        match = re.match(r'\s*model\s*=\s*"([^"]+)"', line)
        if match:
            return match.group(1)
    return ""


# --------------------------------------------------------------------------- parsing


def _add_b64(value, out: list[dict]) -> None:
    if isinstance(value, str) and value.strip():
        out.append({"b64": value.strip()})


def _add_image_value(value, out: list[dict]) -> None:
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.startswith(("http://", "https://")):
            out.append({"url": text})
        else:
            out.append({"b64": text})
    elif isinstance(value, dict):
        if value.get("b64_json"):
            _add_b64(value["b64_json"], out)
        elif value.get("b64"):
            _add_b64(value["b64"], out)
        elif value.get("url"):
            out.append({"url": str(value["url"])})


def items_from_images_payload(payload: dict) -> list[dict]:
    out: list[dict] = []
    for entry in payload.get("data") or []:
        if not isinstance(entry, dict):
            continue
        _add_image_value(entry, out)
    return out


def items_from_responses_payload(payload: dict) -> list[dict]:
    out: list[dict] = []
    for entry in payload.get("output") or []:
        if isinstance(entry, dict) and entry.get("type") == "image_generation_call":
            _add_image_value(entry.get("result"), out)
            if not out and entry.get("b64_json"):
                _add_b64(entry["b64_json"], out)
    return out


def _collect_images(obj, final: list[dict], partial: list[dict]) -> None:
    if isinstance(obj, dict):
        if obj.get("type") == "image_generation_call":
            _add_image_value(obj.get("result"), final)
        if obj.get("partial_image_b64"):
            _add_b64(obj["partial_image_b64"], partial)
        if obj.get("b64_json"):
            _add_b64(obj["b64_json"], final)
        for key, value in obj.items():
            if key in ("partial_image_b64", "b64_json"):
                continue
            if isinstance(value, (dict, list)):
                _collect_images(value, final, partial)
    elif isinstance(obj, list):
        for value in obj:
            _collect_images(value, final, partial)


def items_from_sse(response) -> list[dict]:
    final: list[dict] = []
    partial: list[dict] = []
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            _collect_images(data, final, partial)
    return final or partial


def dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = item.get("b64") or item.get("url") or ""
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# --------------------------------------------------------------------------- requests


def build_responses_input(prompt: str, refs: list[Path]) -> object:
    if not refs:
        return prompt
    content: list[dict] = [{"type": "input_text", "text": prompt}]
    for ref in refs:
        if not ref.exists():
            raise AppError(f"参考图不存在：{ref}")
        mime = "image/png"
        if ref.suffix.lower() in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif ref.suffix.lower() == ".webp":
            mime = "image/webp"
        data = base64.b64encode(ref.read_bytes()).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:{mime};base64,{data}"})
    return [{"role": "user", "content": content}]


def call_images_route(
    cfg: Config,
    prompt: str,
    size: str,
    quality: str,
    count: int,
    timeout: int,
    retries: int,
) -> list[dict]:
    payload = {
        "model": cfg.image_model,
        "prompt": prompt,
        "size": size,
        "n": count,
    }
    if quality and quality != "auto":
        payload["quality"] = quality
    response = http_open(
        "POST",
        f"{cfg.base_url}/images/generations",
        cfg.api_key,
        payload=payload,
        timeout=timeout,
        retries=retries,
    )
    with response:
        raw = response.read()
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise AppError(f"图片接口返回的不是 JSON：{raw[:300]!r}", EXIT_HTTP) from exc
    return items_from_images_payload(data)


def call_responses_route(
    cfg: Config,
    prompt: str,
    size: str,
    quality: str,
    refs: list[Path],
    model: str,
    timeout: int,
    retries: int,
) -> list[dict]:
    tool = {"type": "image_generation", "size": size}
    if quality and quality != "auto":
        tool["quality"] = quality
    payload = {
        "model": model,
        "input": build_responses_input(prompt, refs),
        "tools": [tool],
        "tool_choice": {"type": "image_generation"},
        "stream": True,
    }
    response = http_open(
        "POST",
        f"{cfg.base_url}/responses",
        cfg.api_key,
        payload=payload,
        accept="text/event-stream",
        timeout=timeout,
        retries=retries,
    )
    return items_from_sse(response)


def encode_multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes, str]]):
    boundary = "----relayimagegen" + uuid.uuid4().hex
    body = bytearray()
    for name, value in fields:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += value.encode("utf-8") + b"\r\n"
    for name, filename, content, mime in files:
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def call_edits_route(
    cfg: Config,
    prompt: str,
    size: str,
    quality: str,
    images: list[Path],
    timeout: int,
    retries: int,
) -> list[dict]:
    fields = [("model", cfg.image_model), ("prompt", prompt), ("size", size)]
    if quality and quality != "auto":
        fields.append(("quality", quality))
    files = []
    for image in images:
        if not image.exists():
            raise AppError(f"输入图片不存在：{image}")
        mime = "image/png"
        if image.suffix.lower() in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif image.suffix.lower() == ".webp":
            mime = "image/webp"
        files.append(("image[]", image.name, image.read_bytes(), mime))
    body, content_type = encode_multipart(fields, files)
    response = http_open(
        "POST",
        f"{cfg.base_url}/images/edits",
        cfg.api_key,
        body=body,
        content_type=content_type,
        timeout=timeout,
        retries=retries,
    )
    with response:
        raw = response.read()
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise AppError(f"改图接口返回的不是 JSON：{raw[:300]!r}", EXIT_HTTP) from exc
    return items_from_images_payload(data)


def download(url: str, api_key: str, timeout: int) -> bytes:
    attempt = 0
    while True:
        attempt += 1
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "relay-imagegen/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) and api_key:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "relay-imagegen/1.0",
                        "Authorization": f"Bearer {api_key}",
                    },
                )
                try:
                    with urllib.request.urlopen(request, timeout=timeout) as response:
                        return response.read()
                except Exception:  # noqa: BLE001
                    pass
            if exc.code in RETRYABLE_STATUS and attempt <= 2:
                time.sleep(_seconds(attempt))
                continue
            raise AppError(f"下载图片失败 HTTP {exc.code}: {url}", EXIT_HTTP) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt <= 2:
                time.sleep(_seconds(attempt))
                continue
            raise AppError(f"下载图片失败：{exc}", EXIT_HTTP) from exc


def save_items(
    items: list[dict],
    out_path: Path | None,
    out_dir: Path,
    timeout: int,
    api_key: str,
) -> list[Path]:
    items = dedupe(items)
    if not items:
        raise AppError("接口返回成功，但响应里没有图片数据（既没有 b64，也没有 url）", EXIT_NO_IMAGE)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for index, item in enumerate(items, start=1):
        if out_path is not None:
            path = out_path
            if len(items) > 1:
                path = out_path.with_name(f"{out_path.stem}-{index:02d}{out_path.suffix or '.png'}")
        else:
            path = out_dir / f"relay-image-{stamp}-{index:02d}.png"
        if item.get("b64"):
            value = item["b64"]
            if "," in value and value.lstrip().startswith("data:"):
                value = value.split(",", 1)[1]
            try:
                path.write_bytes(base64.b64decode(value))
            except Exception as exc:  # noqa: BLE001
                raise AppError(f"base64 解码失败：{exc}", EXIT_NO_IMAGE) from exc
        elif item.get("url"):
            path.write_bytes(download(item["url"], api_key, timeout))
        saved.append(path.resolve())
    return saved


# --------------------------------------------------------------------------- commands


def resolve_image_model(cfg: Config, timeout: int) -> str:
    if cfg.image_model and cfg.image_model.lower() not in ("auto", "default"):
        return cfg.image_model
    try:
        models = get_models(cfg, timeout=min(timeout, 30))
    except AppError as exc:
        eprint(f"[warn] 读取模型列表失败，默认使用 gpt-image-2：{exc}")
        return "gpt-image-2"
    return pick_image_model(models)


def resolve_responses_model(cfg: Config) -> str:
    if cfg.responses_model:
        return cfg.responses_model
    model = read_codex_model()
    if model:
        return model
    raise AppError(
        "responses 路由需要一个对话模型：请在 config.env 里设置 RESPONSES_MODEL=...，"
        "或保证 ~/.codex/config.toml 顶层有 model = \"...\""
    )


def run_generate(cfg: Config, args: argparse.Namespace) -> int:
    image_model = resolve_image_model(cfg, args.timeout)
    cfg.image_model = image_model
    refs = [Path(p) for p in (getattr(args, "ref", None) or [])]
    configured_route = getattr(args, "route", None) or cfg.route
    order = ["images", "responses"] if configured_route == "auto" else [configured_route]
    if refs:
        order = [route for route in order if route == "responses"]
        if not order:
            order = ["responses"]
            eprint("[info] 有参考图时只能走 responses 路由，已自动切换")
    if image_model.lower() in ("auto", "default"):
        image_model = "gpt-image-2"

    if args.dry_run:
        print(json.dumps(
            {
                "dry_run": True,
                "base_url": cfg.base_url,
                "routes": order,
                "image_model": image_model,
                "responses_model": cfg.responses_model or "<codex config.toml model>",
                "size": args.size,
                "quality": args.quality,
                "refs": [str(r) for r in refs],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return EXIT_OK

    errors: list[str] = []
    started = time.time()
    for route in order:
        try:
            if route == "images":
                items = call_images_route(
                    cfg, args.prompt, args.size, args.quality, args.n, args.timeout, args.retries
                )
            else:
                model = resolve_responses_model(cfg)
                items = []
                for _ in range(max(1, args.n)):
                    items.extend(
                        call_responses_route(
                            cfg, args.prompt, args.size, args.quality, refs, model, args.timeout, args.retries
                        )
                    )
            if not items:
                errors.append(f"{route}: 没有图片数据")
                continue
            saved = save_items(items, Path(args.out) if args.out else None, cfg.out_dir, args.timeout, cfg.api_key)
            return emit_success(cfg, route, image_model, saved, started, args)
        except AppError as exc:
            errors.append(f"{route}: {exc}")
            if route == "images" and configured_route == "auto":
                eprint(f"[warn] /images/generations 失败，尝试 /responses 兜底：{exc}")
            elif route == "responses" and configured_route == "auto":
                eprint(f"[warn] /responses 失败：{exc}")
    eprint("所有路由都失败：")
    for line in errors:
        eprint("  - " + line)
    return EXIT_ALL_ROUTES


def emit_success(cfg: Config, route: str, model: str, saved: list[Path], started: float, args) -> int:
    elapsed = int((time.time() - started) * 1000)
    payload = {
        "ok": True,
        "route": route,
        "model": model,
        "files": [str(p) for p in saved],
        "markdown": [f"![image]({p.as_posix()})" for p in saved],
        "elapsed_ms": elapsed,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("Saved image(s):")
        for path in saved:
            print(str(path))
        print("\nMarkdown preview:")
        for line in payload["markdown"]:
            print(line)
    return EXIT_OK


def run_edit(cfg: Config, args: argparse.Namespace) -> int:
    cfg.image_model = resolve_image_model(cfg, args.timeout)
    images = [Path(p) for p in args.image]
    if args.dry_run:
        print(json.dumps(
            {"dry_run": True, "base_url": cfg.base_url, "image_model": cfg.image_model,
             "images": [str(i) for i in images], "size": args.size},
            ensure_ascii=False, indent=2,
        ))
        return EXIT_OK
    started = time.time()
    try:
        items = call_edits_route(cfg, args.prompt, args.size, args.quality, images, args.timeout, args.retries)
        saved = save_items(items, Path(args.out) if args.out else None, cfg.out_dir, args.timeout, cfg.api_key)
        return emit_success(cfg, "images-edit", cfg.image_model, saved, started, args)
    except AppError as exc:
        eprint(f"[warn] /images/edits 失败：{exc}")
        eprint("[info] 尝试用 responses 路由 + 参考图兜底")
    try:
        model = resolve_responses_model(cfg)
        items = call_responses_route(
            cfg, args.prompt, args.size, args.quality, images, model, args.timeout, args.retries
        )
        saved = save_items(items, Path(args.out) if args.out else None, cfg.out_dir, args.timeout, cfg.api_key)
        return emit_success(cfg, "responses-ref", cfg.image_model, saved, started, args)
    except AppError as exc:
        eprint(f"改图失败：{exc}")
        return exc.code


def run_probe(cfg: Config, args: argparse.Namespace) -> int:
    report: dict = {
        "config": str(cfg.config_path),
        "config_exists": cfg.config_path.exists(),
        "base_url": cfg.base_url,
        "api_key": cfg.masked_key(),
        "route": cfg.route,
        "image_model": cfg.image_model,
        "responses_model": cfg.responses_model or None,
        "out_dir": str(cfg.out_dir),
    }
    models: list[str] = []
    models_error = None
    try:
        models = get_models(cfg)
        report["models"] = models[:200]
        report["model_count"] = len(models)
        report["image_model_candidates"] = [m for m in models if re.search(r"image", m, re.IGNORECASE)]
        report["picked_image_model"] = pick_image_model(models)
    except AppError as exc:
        models_error = str(exc)
        report["models_error"] = models_error
    report["codex_model"] = read_codex_model() or None
    if not cfg.responses_model and not report["codex_model"]:
        report["responses_route_warning"] = "没有 RESPONSES_MODEL，也没有读到 ~/.codex/config.toml 的 model"

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("== relay-imagegen probe ==")
        print(f"config file : {report['config']} (exists={report['config_exists']})")
        print(f"base url    : {cfg.base_url}")
        print(f"api key     : {cfg.masked_key()}")
        print(f"route       : {cfg.route}")
        print(f"out dir     : {cfg.out_dir}")
        if models_error:
            print(f"models      : FAILED -> {models_error}")
        else:
            print(f"models      : {len(models)} found")
            hits = [m for m in models if re.search(r"image", m, re.IGNORECASE)]
            print(f"image models: {', '.join(hits[:12]) if hits else '(none visible in /v1/models)'}")
            print(f"pick        : {pick_image_model(models)}")
        print(f"codex model : {report['codex_model'] or '(not found)'}")
        print()
        print("suggested next commands:")
        print(
            '  py imagegen.py generate --prompt "一只坐在窗台上的橘猫，写实风格" '
            '--size 1024x1024 --quality low'
        )
    if args.test:
        print("\n== generating one low-quality test image ==")
        args.prompt = "A small blue flower illustration on a clean white background"
        args.size = "1024x1024"
        args.quality = "low"
        args.n = 1
        args.ref = []
        args.out = None
        args.route = None
        args.dry_run = False
        return run_generate(cfg, args)
    return EXIT_OK


# --------------------------------------------------------------------------- cli


def _global_options(suppress_defaults: bool) -> argparse.ArgumentParser:
    """Shared global options. Sub-parser copies suppress defaults so the top-level value
    (given before the subcommand) is not overwritten by sub-parser defaults."""
    parser = argparse.ArgumentParser(add_help=False)
    default = (lambda value: argparse.SUPPRESS) if suppress_defaults else (lambda value: value)
    parser.add_argument("--config", default=default(str(DEFAULT_CONFIG)), help="config.env path")
    parser.add_argument("--timeout", type=int, default=default(300), help="request timeout seconds")
    parser.add_argument("--retries", type=int, default=default(2), help="retry count for 429/5xx/timeouts")
    parser.add_argument("--json", action="store_true", default=default(False), help="machine-readable output")
    return parser


def build_parser() -> argparse.ArgumentParser:
    root_common = _global_options(suppress_defaults=False)
    sub_common = _global_options(suppress_defaults=True)

    parser = argparse.ArgumentParser(
        prog="imagegen",
        description="Generate or edit images through your own OpenAI-compatible image relay.",
        parents=[root_common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="text-to-image", parents=[sub_common])
    generate.add_argument("--prompt", "-p", required=True)
    generate.add_argument("--size", default="1024x1024")
    generate.add_argument("--quality", default="medium", choices=["low", "medium", "high", "auto"])
    generate.add_argument("--n", type=int, default=1)
    generate.add_argument("--out", help="exact output file path")
    generate.add_argument("--ref", action="append", default=[], help="reference image (responses route)")
    generate.add_argument("--route", choices=["auto", "images", "responses"], default=None)
    generate.add_argument("--dry-run", action="store_true")

    edit = sub.add_parser("edit", help="image edit / img2img", parents=[sub_common])
    edit.add_argument("--prompt", "-p", required=True)
    edit.add_argument("--image", "-i", action="append", required=True, help="input image (repeatable)")
    edit.add_argument("--size", default="1024x1024")
    edit.add_argument("--quality", default="medium", choices=["low", "medium", "high", "auto"])
    edit.add_argument("--out", help="exact output file path")
    edit.add_argument("--dry-run", action="store_true")

    probe = sub.add_parser(
        "probe", help="check config, model list and route before spending money", parents=[sub_common]
    )
    probe.add_argument("--test", action="store_true", help="also generate one low-quality test image")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = Config(Path(args.config), args)
        if args.command == "probe":
            return run_probe(cfg, args)
        if args.command == "generate":
            if args.n < 1 or args.n > 10:
                raise AppError("--n 必须在 1..10 之间")
            return run_generate(cfg, args)
        if args.command == "edit":
            return run_edit(cfg, args)
        raise AppError(f"未知命令：{args.command}")
    except AppError as exc:
        eprint(f"Error: {exc}")
        return exc.code
    except KeyboardInterrupt:
        eprint("Interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
