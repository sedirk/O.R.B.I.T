import json
import locale
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests
import cv2

from homebox import HomeboxClient, HomeboxError
from labels import canonical_asset_code, display_code, item_url, print_item_label_set, rfid_epc_code, rfid_epc_hex, rfid_payload
from rfid_e710 import E710Error, E710Reader
from scale import ElectronicScaleReader

try:
    import pyrealsense2 as rs
except Exception:
    rs = None


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
WEB_ROOT = ROOT / "web"
LOG_DIR = ROOT / "logs"
INTAKE_RECORD_DIR = ROOT / "intake_records"
INTAKE_RECORD_SCHEMA = "orbit.intake_record"
INTAKE_RECORD_VERSION = 1

DEFAULT_HOST = os.getenv("ORBIT_WEB_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("ORBIT_WEB_PORT", "8765"))
DEFAULT_HOMEBOX_URL = os.getenv("HOMEBOX_URL", "http://192.168.31.3:3100")
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/chat")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
DEFAULT_AI_TARGET = os.getenv("ORBIT_AI_TARGET", "local")
DEFAULT_CLOUD_PROVIDER = os.getenv("ORBIT_CLOUD_PROVIDER", "openai")
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
LOCAL_CONFIG_PATH = Path(os.getenv("ORBIT_CONFIG_FILE", str(ROOT / "orbit_runtime.local.json")))


def load_local_runtime_config() -> dict:
    try:
        if not LOCAL_CONFIG_PATH.exists():
            return {}
        data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and isinstance(data.get("config"), dict):
            return dict(data["config"])
        if isinstance(data, dict):
            return dict(data)
    except Exception as exc:
        print(f"⚠️ 本地配置读取失败: {exc}")
    return {}


LOCAL_RUNTIME_CONFIG = load_local_runtime_config()


def config_default(key: str, env_names, fallback=""):
    if isinstance(env_names, str):
        env_names = [env_names]
    for name in env_names:
        value = os.getenv(name)
        if value not in {None, ""}:
            return value
    value = LOCAL_RUNTIME_CONFIG.get(key)
    if value not in {None, ""}:
        return str(value)
    return fallback


def now_ms() -> int:
    return int(time.time() * 1000)


def json_dumps(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_subprocess_output(raw) -> str:
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    encodings = [
        "utf-8-sig",
        "utf-16",
        locale.getpreferredencoding(False),
        "gbk",
        "cp936",
    ]
    seen = set()
    for encoding in encodings:
        key = str(encoding or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_powershell_text(command: str, timeout: int = 8) -> str:
    prefix = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", prefix + command],
        capture_output=True,
        text=False,
        timeout=timeout,
    )
    return decode_subprocess_output(completed.stdout).strip()


def normalize_ollama_base_url(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        text = DEFAULT_OLLAMA_URL
    if not re.match(r"^https?://", text, re.I):
        text = f"http://{text}"
    text = text.rstrip("/")
    text = re.sub(r"/api/chat$", "", text, flags=re.I)
    return text.rstrip("/")


def normalize_ollama_chat_url(value: str | None) -> str:
    return f"{normalize_ollama_base_url(value)}/api/chat"


def infer_ai_target_from_ollama_url(value: str | None) -> str:
    base = normalize_ollama_base_url(value)
    return "local" if base in {"http://127.0.0.1:11434", "http://localhost:11434"} else "lan"


def default_cloud_base(provider: str | None) -> str:
    return DEFAULT_GEMINI_OPENAI_BASE_URL if str(provider or "").strip().lower() == "gemini" else DEFAULT_OPENAI_BASE_URL


def normalize_openai_base_url(value: str | None, provider: str | None = None) -> str:
    text = str(value or "").strip() or default_cloud_base(provider)
    if not re.match(r"^https?://", text, re.I):
        text = f"https://{text}"
    text = text.rstrip("/")
    text = re.sub(r"/chat/completions$", "", text, flags=re.I)
    return text.rstrip("/")


def config_bool(value, default: str = "0") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "启用", "是"}:
        return "1"
    if text in {"0", "false", "no", "off", "禁用", "否"}:
        return "0"
    return default


CONFIG_LOCK = threading.Lock()
RUNTIME_CONFIG = {
    "homebox_url": config_default("homebox_url", "HOMEBOX_URL", DEFAULT_HOMEBOX_URL).rstrip("/"),
    "homebox_token": config_default("homebox_token", "HOMEBOX_TOKEN", ""),
    "homebox_username": config_default("homebox_username", "HOMEBOX_USERNAME", ""),
    "homebox_password": config_default("homebox_password", "HOMEBOX_PASSWORD", ""),
    "ollama_url": normalize_ollama_chat_url(config_default("ollama_url", "OLLAMA_API_URL", DEFAULT_OLLAMA_URL)),
    "ollama_model": config_default("ollama_model", "OLLAMA_MODEL", DEFAULT_MODEL),
    "ai_target": config_default("ai_target", "ORBIT_AI_TARGET", infer_ai_target_from_ollama_url(config_default("ollama_url", "OLLAMA_API_URL", DEFAULT_OLLAMA_URL))),
    "cloud_provider": config_default("cloud_provider", "ORBIT_CLOUD_PROVIDER", DEFAULT_CLOUD_PROVIDER),
    "ai_api_base": normalize_openai_base_url(
        config_default("ai_api_base", "ORBIT_AI_API_BASE", ""),
        config_default("cloud_provider", "ORBIT_CLOUD_PROVIDER", DEFAULT_CLOUD_PROVIDER),
    ),
    "ai_api_key": config_default("ai_api_key", ["ORBIT_AI_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"], ""),
    "mode": config_default("mode", "ORBIT_SCAN_MODE", "auto"),
    "num_predict": config_default("num_predict", "OLLAMA_NUM_PREDICT", "192"),
    "image_max_size": config_default("image_max_size", "ORBIT_AI_IMAGE_MAX_SIZE", "0"),
    "scale_port": config_default("scale_port", "SCALE_PORT", "COM9"),
    "scale_baud": config_default("scale_baud", "SCALE_BAUD", "9600"),
    "rfid_port": config_default("rfid_port", "RFID_PORT", ""),
    "aux_camera_enabled": config_default("aux_camera_enabled", "ORBIT_AUX_CAMERA_ENABLED", "1"),
    "aux_camera_index": config_default("aux_camera_index", "ORBIT_AUX_CAMERA_INDEX", "0"),
    "aux_camera_backend": config_default("aux_camera_backend", "ORBIT_AUX_CAMERA_BACKEND", "dshow"),
    "aux_camera_width": config_default("aux_camera_width", "ORBIT_AUX_CAMERA_WIDTH", "1280"),
    "aux_camera_height": config_default("aux_camera_height", "ORBIT_AUX_CAMERA_HEIGHT", "720"),
    "aux_camera_center_crop": config_default("aux_camera_center_crop", "ORBIT_AUX_CAMERA_CENTER_CROP", "1"),
    "aux_camera_auto_exposure": config_default("aux_camera_auto_exposure", "ORBIT_AUX_CAMERA_AUTO_EXPOSURE", "1"),
    "aux_camera_warmup_seconds": config_default("aux_camera_warmup_seconds", "ORBIT_AUX_CAMERA_WARMUP_SECONDS", "1.2"),
    "print_pet_labels": config_default("print_pet_labels", "ORBIT_PRINT_LABELS", "1"),
    "write_rfid_tags": config_default("write_rfid_tags", "ORBIT_WRITE_RFID", "1"),
}


def config_snapshot() -> dict:
    with CONFIG_LOCK:
        return dict(RUNTIME_CONFIG)


def public_config(config: dict | None = None) -> dict:
    config = dict(config or config_snapshot())
    return {
        "homebox_url": config.get("homebox_url", DEFAULT_HOMEBOX_URL),
        "homebox_username": config.get("homebox_username", ""),
        "homebox_has_token": bool(config.get("homebox_token")),
        "homebox_has_password": bool(config.get("homebox_password")),
        "ollama_url": config.get("ollama_url", DEFAULT_OLLAMA_URL),
        "ollama_model": config.get("ollama_model", DEFAULT_MODEL),
        "ai_target": config.get("ai_target", infer_ai_target_from_ollama_url(config.get("ollama_url", DEFAULT_OLLAMA_URL))),
        "cloud_provider": config.get("cloud_provider", DEFAULT_CLOUD_PROVIDER),
        "ai_api_base": config.get("ai_api_base", default_cloud_base(config.get("cloud_provider"))),
        "ai_has_api_key": bool(config.get("ai_api_key")),
        "mode": config.get("mode", "auto"),
        "num_predict": config.get("num_predict", "192"),
        "image_max_size": config.get("image_max_size", "0"),
        "scale_port": config.get("scale_port", "COM9"),
        "scale_baud": config.get("scale_baud", "9600"),
        "rfid_port": config.get("rfid_port", ""),
        "aux_camera_enabled": config.get("aux_camera_enabled", "1"),
        "aux_camera_index": config.get("aux_camera_index", "0"),
        "aux_camera_backend": config.get("aux_camera_backend", "dshow"),
        "aux_camera_width": config.get("aux_camera_width", "1280"),
        "aux_camera_height": config.get("aux_camera_height", "720"),
        "aux_camera_center_crop": config.get("aux_camera_center_crop", "1"),
        "aux_camera_auto_exposure": config.get("aux_camera_auto_exposure", "1"),
        "aux_camera_warmup_seconds": config.get("aux_camera_warmup_seconds", "1.2"),
        "print_pet_labels": config.get("print_pet_labels", "1"),
        "write_rfid_tags": config.get("write_rfid_tags", "1"),
    }


def save_local_runtime_config(config: dict) -> None:
    try:
        LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "orbit.runtime_config",
            "version": 1,
            "updated_at": now_ms(),
            "config": {key: config.get(key, "") for key in RUNTIME_CONFIG.keys()},
        }
        tmp_path = LOCAL_CONFIG_PATH.with_name(f"{LOCAL_CONFIG_PATH.name}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, LOCAL_CONFIG_PATH)
    except Exception as exc:
        print(f"⚠️ 本地配置保存失败: {exc}")


def runtime_homebox_client(timeout: int = 20) -> HomeboxClient:
    config = config_snapshot()
    return HomeboxClient(
        config.get("homebox_url", DEFAULT_HOMEBOX_URL),
        token=config.get("homebox_token") or os.getenv("HOMEBOX_TOKEN"),
        username=config.get("homebox_username") or os.getenv("HOMEBOX_USERNAME"),
        password=config.get("homebox_password") or os.getenv("HOMEBOX_PASSWORD"),
        timeout=timeout,
    )


def list_openai_compatible_models(provider: str, base_url: str, api_key: str, timeout: int = 5) -> dict:
    provider = (provider or "openai").strip().lower()
    base = normalize_openai_base_url(base_url, provider)
    result = {"ok": False, "provider": provider, "base_url": base, "models": [], "ps": "", "message": ""}
    if not api_key:
        result["message"] = "未配置云端 API Key"
        return result
    try:
        response = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        rows = data.get("data") or data.get("models") or []
        models = []
        for row in rows:
            if isinstance(row, dict):
                name = row.get("id") or row.get("name")
            else:
                name = str(row)
            if name:
                models.append(str(name))
        result["models"] = sorted(set(models), key=str.lower)
        result["ok"] = True
        result["ps"] = "云端模型列表已读取"
    except Exception as exc:
        result["message"] = f"读取云端模型列表失败: {exc}"
    return result


def update_runtime_config(payload: dict) -> tuple[dict, bool]:
    previous = config_snapshot()
    updates = {}

    mode = str(payload.get("mode") or "").strip()
    if mode in {"scale", "auto", "macro", "full", "furniture"}:
        updates["mode"] = mode

    homebox_url = str(payload.get("homebox_url") or "").strip().rstrip("/")
    if homebox_url:
        if not re.match(r"^https?://", homebox_url, re.I):
            homebox_url = f"http://{homebox_url}"
        updates["homebox_url"] = homebox_url.rstrip("/")

    homebox_username = str(payload.get("homebox_username") or "").strip()
    if homebox_username:
        updates["homebox_username"] = homebox_username

    homebox_password = str(payload.get("homebox_password") or "")
    if homebox_password:
        updates["homebox_password"] = homebox_password

    homebox_token = str(payload.get("homebox_token") or "").strip()
    if homebox_token:
        updates["homebox_token"] = homebox_token

    ai_target = str(payload.get("ai_target") or "").strip().lower()
    if ai_target in {"local", "lan", "cloud"}:
        updates["ai_target"] = ai_target

    cloud_provider = str(payload.get("cloud_provider") or payload.get("ai_provider") or "").strip().lower()
    if cloud_provider in {"openai", "gemini"}:
        updates["cloud_provider"] = cloud_provider

    active_cloud_provider = updates.get("cloud_provider") or previous.get("cloud_provider") or DEFAULT_CLOUD_PROVIDER
    ai_api_base = str(payload.get("ai_api_base") or "").strip()
    if ai_api_base:
        updates["ai_api_base"] = normalize_openai_base_url(ai_api_base, active_cloud_provider)

    ai_api_key = str(payload.get("ai_api_key") or "").strip()
    if ai_api_key:
        updates["ai_api_key"] = ai_api_key

    ollama_url = str(payload.get("ollama_url") or "").strip()
    if ollama_url:
        updates["ollama_url"] = normalize_ollama_chat_url(ollama_url)

    model = str(payload.get("ollama_model") or payload.get("model") or "").strip()
    if model:
        updates["ollama_model"] = model

    for key in ("num_predict", "image_max_size", "scale_baud", "aux_camera_width", "aux_camera_height"):
        if payload.get(key) is None:
            continue
        try:
            value = max(0, int(float(payload.get(key))))
        except (TypeError, ValueError):
            continue
        if key == "scale_baud" and value <= 0:
            continue
        updates[key] = str(value)

    scale_port = str(payload.get("scale_port") or "").strip()
    if scale_port:
        updates["scale_port"] = scale_port

    rfid_port = str(payload.get("rfid_port") or "").strip()
    if rfid_port:
        updates["rfid_port"] = rfid_port

    aux_index = str(payload.get("aux_camera_index") or "").strip()
    if aux_index:
        updates["aux_camera_index"] = aux_index

    aux_backend = str(payload.get("aux_camera_backend") or "").strip().lower()
    if aux_backend in {"auto", "dshow", "msmf", "any"}:
        updates["aux_camera_backend"] = aux_backend

    if payload.get("aux_camera_center_crop") is not None:
        try:
            updates["aux_camera_center_crop"] = str(min(1.0, max(0.35, float(payload.get("aux_camera_center_crop")))))
        except (TypeError, ValueError):
            pass

    if payload.get("aux_camera_warmup_seconds") is not None:
        try:
            updates["aux_camera_warmup_seconds"] = str(min(5.0, max(0.0, float(payload.get("aux_camera_warmup_seconds")))))
        except (TypeError, ValueError):
            pass

    for key, default in (
        ("aux_camera_enabled", "1"),
        ("aux_camera_auto_exposure", "1"),
        ("print_pet_labels", "1"),
        ("write_rfid_tags", "1"),
    ):
        if key in payload:
            updates[key] = config_bool(payload.get(key), previous.get(key, default))

    with CONFIG_LOCK:
        RUNTIME_CONFIG.update(updates)
        current = dict(RUNTIME_CONFIG)

    os.environ["HOMEBOX_URL"] = current["homebox_url"]
    if current.get("homebox_token"):
        os.environ["HOMEBOX_TOKEN"] = current["homebox_token"]
    if current.get("homebox_username"):
        os.environ["HOMEBOX_USERNAME"] = current["homebox_username"]
    if current.get("homebox_password"):
        os.environ["HOMEBOX_PASSWORD"] = current["homebox_password"]
    os.environ["ORBIT_AI_TARGET"] = current["ai_target"]
    os.environ["ORBIT_CLOUD_PROVIDER"] = current["cloud_provider"]
    os.environ["ORBIT_AI_PROVIDER"] = "ollama" if current["ai_target"] in {"local", "lan"} else current["cloud_provider"]
    os.environ["ORBIT_AI_API_BASE"] = current["ai_api_base"]
    if current.get("ai_api_key"):
        os.environ["ORBIT_AI_API_KEY"] = current["ai_api_key"]
    os.environ["OLLAMA_API_URL"] = current["ollama_url"]
    os.environ["OLLAMA_MODEL"] = current["ollama_model"]
    os.environ["ORBIT_SCAN_MODE"] = current["mode"]
    os.environ["OLLAMA_NUM_PREDICT"] = current["num_predict"]
    os.environ["ORBIT_AI_IMAGE_MAX_SIZE"] = current["image_max_size"]
    os.environ["SCALE_PORT"] = current["scale_port"]
    os.environ["SCALE_BAUD"] = current["scale_baud"]
    os.environ["RFID_PORT"] = current["rfid_port"]
    os.environ["ORBIT_AUX_CAMERA_ENABLED"] = current["aux_camera_enabled"]
    os.environ["ORBIT_AUX_CAMERA_INDEX"] = current["aux_camera_index"]
    os.environ["ORBIT_AUX_CAMERA_BACKEND"] = current["aux_camera_backend"]
    os.environ["ORBIT_AUX_CAMERA_WIDTH"] = current["aux_camera_width"]
    os.environ["ORBIT_AUX_CAMERA_HEIGHT"] = current["aux_camera_height"]
    os.environ["ORBIT_AUX_CAMERA_CENTER_CROP"] = current["aux_camera_center_crop"]
    os.environ["ORBIT_AUX_CAMERA_AUTO_EXPOSURE"] = current["aux_camera_auto_exposure"]
    os.environ["ORBIT_AUX_CAMERA_WARMUP_SECONDS"] = current["aux_camera_warmup_seconds"]
    os.environ["ORBIT_PRINT_LABELS"] = current["print_pet_labels"]
    os.environ["ORBIT_WRITE_RFID"] = current["write_rfid_tags"]
    save_local_runtime_config(current)

    scale_changed = (
        previous.get("scale_port") != current.get("scale_port")
        or previous.get("scale_baud") != current.get("scale_baud")
    )
    return current, scale_changed


def base_env() -> dict:
    env = os.environ.copy()
    config = config_snapshot()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["HOMEBOX_URL"] = config["homebox_url"]
    if config.get("homebox_token"):
        env["HOMEBOX_TOKEN"] = config["homebox_token"]
    if config.get("homebox_username"):
        env["HOMEBOX_USERNAME"] = config["homebox_username"]
    if config.get("homebox_password"):
        env["HOMEBOX_PASSWORD"] = config["homebox_password"]
    env["ORBIT_AI_TARGET"] = config["ai_target"]
    env["ORBIT_CLOUD_PROVIDER"] = config["cloud_provider"]
    env["ORBIT_AI_PROVIDER"] = "ollama" if config["ai_target"] in {"local", "lan"} else config["cloud_provider"]
    env["ORBIT_AI_API_BASE"] = config["ai_api_base"]
    if config.get("ai_api_key"):
        env["ORBIT_AI_API_KEY"] = config["ai_api_key"]
    env["OLLAMA_API_URL"] = config["ollama_url"]
    env["OLLAMA_MODEL"] = config["ollama_model"]
    env.setdefault("OLLAMA_NUM_GPU", "36")
    env.setdefault("OLLAMA_NUM_CTX", "2048")
    env["OLLAMA_NUM_PREDICT"] = config["num_predict"]
    env.setdefault("OLLAMA_TIMEOUT", "150")
    env.setdefault("OLLAMA_KEEP_ALIVE", "15m")
    env["ORBIT_SCAN_MODE"] = config["mode"]
    env.setdefault("ORBIT_SCALE_AI_ROTATE", "180")
    env.setdefault("ORBIT_SEGMENT_MAX_SIZE", "960")
    env.setdefault("ORBIT_EXPOSURE_RETRY", "1")
    env.setdefault("ORBIT_AI_ENHANCE", "1")
    env.setdefault("ORBIT_AI_COMPOSITE_VIEW", "0")
    env["ORBIT_AI_IMAGE_MAX_SIZE"] = config["image_max_size"]
    env.setdefault("ORBIT_AI_IMAGE_JPEG_QUALITY", "80")
    env.setdefault("ORBIT_EXPOSURE_ROI", "0")
    env["SCALE_PORT"] = config["scale_port"]
    env["SCALE_BAUD"] = config["scale_baud"]
    env["RFID_PORT"] = config["rfid_port"]
    env["ORBIT_AUX_CAMERA_ENABLED"] = config["aux_camera_enabled"]
    env["ORBIT_AUX_CAMERA_INDEX"] = config["aux_camera_index"]
    env["ORBIT_AUX_CAMERA_BACKEND"] = config["aux_camera_backend"]
    env["ORBIT_AUX_CAMERA_WIDTH"] = config["aux_camera_width"]
    env["ORBIT_AUX_CAMERA_HEIGHT"] = config["aux_camera_height"]
    env["ORBIT_AUX_CAMERA_CENTER_CROP"] = config["aux_camera_center_crop"]
    env["ORBIT_AUX_CAMERA_AUTO_EXPOSURE"] = config["aux_camera_auto_exposure"]
    env["ORBIT_AUX_CAMERA_WARMUP_SECONDS"] = config["aux_camera_warmup_seconds"]
    env.setdefault("ORBIT_AUX_RECAPTURE_ON_SELECTION", "0")
    env["ORBIT_PRINT_LABELS"] = config["print_pet_labels"]
    env["ORBIT_WRITE_RFID"] = config["write_rfid_tags"]
    return env


class ScaleMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.reader = None
        self.running = False
        self.thread = None
        self.recent = []
        self.state = {
            "connected": False,
            "port": os.getenv("SCALE_PORT", "auto"),
            "weight_g": None,
            "stable": False,
            "raw": "",
            "updated_at": None,
            "error": None,
        }

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="scale-monitor", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.reader:
            try:
                self.reader.close()
            except Exception:
                pass

    def reconfigure(self):
        with self.lock:
            self.recent = []
            self.state.update(
                {
                    "connected": False,
                    "port": config_snapshot().get("scale_port", "auto"),
                    "stable": False,
                    "raw": "",
                    "updated_at": now_ms(),
                    "error": "串口配置已更新，正在重连",
                }
            )
        if self.reader:
            try:
                self.reader.close()
            except Exception:
                pass
            self.reader = None

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def _set_state(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)

    def _open_reader(self):
        config = config_snapshot()
        port = config.get("scale_port") or os.getenv("SCALE_PORT", "auto")
        baud = int(config.get("scale_baud") or os.getenv("SCALE_BAUD", "9600"))
        self.reader = ElectronicScaleReader(port=port, baudrate=baud, timeout=0.25)
        self._set_state(connected=True, port=self.reader.port, error=None)

    def _loop(self):
        while self.running:
            try:
                if self.reader is None:
                    self._open_reader()

                reading = self.reader.read_once()
                if reading is None:
                    continue

                t = time.time()
                self.recent.append((t, reading.weight_g))
                self.recent = [(ts, w) for ts, w in self.recent if t - ts <= 1.6]
                stable = False
                if len(self.recent) >= 4:
                    weights = [w for _, w in self.recent[-6:]]
                    stable = max(weights) - min(weights) <= float(os.getenv("SCALE_STABLE_TOLERANCE_G", "0.35"))

                self._set_state(
                    connected=True,
                    port=self.reader.port,
                    weight_g=reading.weight_g,
                    stable=bool(stable or reading.stable),
                    raw=reading.raw,
                    updated_at=now_ms(),
                    error=None,
                )
            except Exception as exc:
                self._set_state(connected=False, error=str(exc), updated_at=now_ms())
                if self.reader:
                    try:
                        self.reader.close()
                    except Exception:
                        pass
                    self.reader = None
                time.sleep(2.0)


class TaskRunner:
    def __init__(self):
        self.lock = threading.Lock()
        self.task = None
        self.process = None
        self.pending_item = None

    def _timeout_for_action(self, action: str) -> int:
        defaults = {
            "capture": 75,
            "scan": 190,
            "identify_selection": 190,
            "diagnose": 120,
            "ollama_restart": 25,
        }
        env_key = f"ORBIT_{action.upper()}_TIMEOUT_SECONDS"
        raw = os.getenv(env_key) or os.getenv("ORBIT_TASK_TIMEOUT_SECONDS") or str(defaults.get(action, 210))
        try:
            return max(10, int(float(raw)))
        except ValueError:
            return defaults.get(action, 210)

    def snapshot(self):
        with self.lock:
            self._reap_stale_task_locked()
            if not self.task:
                return {"running": False, "pending_item": self.pending_item}
            data = dict(self.task)
            data["logs"] = list(self.task.get("logs", []))[-220:]
            data["pending_item"] = self.pending_item
            return data

    def _reap_stale_task_locked(self):
        if not self.task or not self.task.get("running"):
            return
        proc = self.process
        elapsed_ms = now_ms() - int(self.task.get("started_at") or now_ms())
        timeout_ms = int(self.task.get("timeout_seconds") or 0) * 1000

        if proc is None:
            if elapsed_ms <= 3000:
                return
            self.task["running"] = False
            self.task["returncode"] = -10
            self.task["finished_at"] = now_ms()
            self.task["error"] = "任务进程已退出但状态未收尾，已自动回收"
            self.task.setdefault("logs", []).append("🧹 任务进程已不存在，GUI 已自动回收卡住状态。")
            return

        returncode = proc.poll()
        if returncode is not None:
            self.task["running"] = False
            self.task["returncode"] = returncode
            self.task["finished_at"] = now_ms()
            self.task.setdefault("logs", []).append(f"🧹 任务进程已结束，GUI 已补记状态 code {returncode}。")
            return

        if timeout_ms and elapsed_ms > timeout_ms + 5000:
            self.task["timed_out"] = True
            self.task["running"] = False
            self.task["returncode"] = -9
            self.task["finished_at"] = now_ms()
            self.task["error"] = f"任务超过 {self.task.get('timeout_seconds')}s 自动终止"
            self.task.setdefault("logs", []).append(f"⏱️ 任务超过 {self.task.get('timeout_seconds')}s，状态接口已强制回收。")
            self._terminate_process_tree(proc)
            self.process = None

    def start(self, action: str, payload: dict, scale_status: dict):
        with self.lock:
            self._reap_stale_task_locked()
            if self.task and self.task.get("running"):
                return False, "已有任务正在运行"

            command, label = self._build_command(action, payload, scale_status)
            if not command:
                return False, "未知动作"
            timeout_seconds = self._timeout_for_action(action)

            request_selection = self._selection_from_payload(payload.get("bbox")) if action == "identify_selection" else None
            self.task = {
                "id": str(uuid.uuid4())[:8],
                "action": action,
                "label": label,
                "timeout_seconds": timeout_seconds,
                "timed_out": False,
                "running": True,
                "returncode": None,
                "started_at": now_ms(),
                "finished_at": None,
                "logs": [],
                "latest_image": None,
                "latest_aux_image": None,
                "latest_result": None,
                "latest_selection": request_selection,
                "request_selection": request_selection,
                "error": None,
            }
            if action in {"scan", "identify_selection"}:
                self.pending_item = None

            thread = threading.Thread(
                target=self._run_process,
                args=(command, timeout_seconds),
                name=f"task-{action}",
                daemon=True,
            )
            thread.start()
            return True, self.task["id"]

    def cancel(self):
        with self.lock:
            self._reap_stale_task_locked()
            proc = self.process
            if self.task and not self.task.get("running"):
                return True, "已回收卡住任务状态"
        if not proc or proc.poll() is not None:
            return False, "没有正在运行的任务"
        self._terminate_process_tree(proc)
        return True, "已请求取消"

    def _build_command(self, action: str, payload: dict, scale_status: dict):
        python = sys.executable
        main_py = str(ROOT / "main.py")
        config = config_snapshot()
        mode = payload.get("mode") or config.get("mode") or "auto"
        model = payload.get("model") or payload.get("ollama_model") or config.get("ollama_model") or DEFAULT_MODEL
        num_predict = str(payload.get("num_predict") or config.get("num_predict") or "192")
        homebox_url = str(payload.get("homebox_url") or config.get("homebox_url") or DEFAULT_HOMEBOX_URL).rstrip("/")
        ollama_url = normalize_ollama_chat_url(payload.get("ollama_url") or config.get("ollama_url") or DEFAULT_OLLAMA_URL)
        ai_target = str(payload.get("ai_target") or config.get("ai_target") or DEFAULT_AI_TARGET).strip().lower()
        cloud_provider = str(payload.get("cloud_provider") or config.get("cloud_provider") or DEFAULT_CLOUD_PROVIDER).strip().lower()
        ai_provider = "ollama" if ai_target in {"local", "lan"} else cloud_provider
        ai_api_base = normalize_openai_base_url(payload.get("ai_api_base") or config.get("ai_api_base"), cloud_provider)
        image_max_size = str(
            payload.get("image_max_size")
            if payload.get("image_max_size") is not None
            else config.get("image_max_size", "0")
        )
        common = [
            "--homebox-url",
            homebox_url,
            "--mode",
            mode,
            "--ollama-url",
            ollama_url,
            "--ollama-model",
            model,
            "--ai-provider",
            ai_provider,
            "--ai-api-base",
            ai_api_base,
            "--ollama-num-gpu",
            os.getenv("OLLAMA_NUM_GPU", "36"),
            "--ollama-num-ctx",
            os.getenv("OLLAMA_NUM_CTX", "2048"),
            "--ollama-num-predict",
            num_predict,
            "--ollama-timeout",
            os.getenv("OLLAMA_TIMEOUT", "150"),
            "--ai-image-max-size",
            image_max_size,
        ]

        if action == "capture":
            return [python, main_py, "capture", *common], "主相机拍照"

        if action == "scan":
            weight = payload.get("weight_g")
            if weight is None:
                weight = scale_status.get("weight_g")
            command = [python, main_py, "scan-once", *common]
            command.append("--dry-run")
            if weight is not None:
                command.extend(["--weight-g", str(weight)])
            return command, "识别物品"

        if action == "identify_selection":
            image_name = Path(str(payload.get("image_name") or "")).name
            aux_name = Path(str(payload.get("aux_image_name") or "")).name
            result_name = Path(str(payload.get("result_name") or "")).name
            bbox = payload.get("bbox") or {}
            reexpose = payload.get("reexpose", True)
            if not image_name:
                return None, None
            image_path = LOG_DIR / image_name
            if not image_path.exists():
                return None, None

            subcommand = "identify-selection-live" if reexpose else "identify-image"
            command = [python, main_py, subcommand, *common, "--dry-run", "--image", str(image_path)]
            if aux_name and (LOG_DIR / aux_name).exists():
                command.extend(["--aux-image", str(LOG_DIR / aux_name)])
            if result_name and (LOG_DIR / result_name).exists():
                command.extend(["--measurement-json", str(LOG_DIR / result_name)])
            if all(k in bbox for k in ("x", "y", "w", "h")):
                command.extend(["--bbox", f"{bbox['x']},{bbox['y']},{bbox['w']},{bbox['h']}"])
            weight = payload.get("weight_g")
            if weight is None:
                weight = scale_status.get("weight_g")
            if weight is not None:
                command.extend(["--weight-g", str(weight)])
            return command, "识别物品"

        if action == "diagnose":
            return [python, main_py, "diagnose", "--dry-run", *common], "系统诊断"

        if action == "ollama_restart":
            script = str(ROOT / "start_ollama_vulkan.ps1")
            return [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script,
                "-Restart",
            ], "重启 Ollama"

        return None, None

    def _append_log(self, line: str):
        with self.lock:
            if not self.task:
                return
            self.task.setdefault("logs", []).append(line.rstrip())
            self.task["logs"] = self.task["logs"][-260:]
            match = re.search(r"(?:capture|scan) image saved:\s*(.+?\.jpg)", line, re.I)
            if match:
                self.task["latest_image"] = Path(match.group(1).strip()).name
            result_match = re.search(r"(?:scan|capture) result saved:\s*(.+?\.json)", line, re.I)
            if result_match:
                result_path = Path(result_match.group(1).strip())
                self.task["latest_result"] = result_path.name
                selection = self._selection_from_result(result_path)
                if selection:
                    self.task["latest_selection"] = selection
            aux_match = re.search(r"(?:C270|辅助相机) 辅助视角已采集:\s*([^,\s]+\.jpg)", line, re.I)
            if aux_match:
                self.task["latest_aux_image"] = Path(aux_match.group(1).strip()).name

    def _selection_from_payload(self, bbox):
        if not isinstance(bbox, dict):
            return None
        try:
            x = float(bbox.get("x"))
            y = float(bbox.get("y"))
            w = float(bbox.get("w"))
            h = float(bbox.get("h"))
        except (TypeError, ValueError):
            return None
        x = max(0.0, min(0.99, x))
        y = max(0.0, min(0.99, y))
        w = max(0.01, min(1.0 - x, w))
        h = max(0.01, min(1.0 - y, h))
        return {"x": x, "y": y, "w": w, "h": h, "source": "manual"}

    def _selection_from_result(self, result_path: Path):
        try:
            if not result_path.exists():
                result_path = LOG_DIR / result_path.name
            data = json.loads(result_path.read_text(encoding="utf-8"))
            bbox = (data.get("measurement") or {}).get("selection_bbox")
            if not isinstance(bbox, dict) or not bbox:
                return None
            image_w = float(bbox.get("image_w") or 0)
            image_h = float(bbox.get("image_h") or 0)
            x = float(bbox.get("x") or 0)
            y = float(bbox.get("y") or 0)
            w = float(bbox.get("w") or 1)
            h = float(bbox.get("h") or 1)
            if x <= 1 and y <= 1 and w <= 1 and h <= 1:
                return {
                    "x": max(0.0, min(1.0, x)),
                    "y": max(0.0, min(1.0, y)),
                    "w": max(0.01, min(1.0, w)),
                    "h": max(0.01, min(1.0, h)),
                    "source": bbox.get("source") or "manual",
                }
            if image_w <= 0 or image_h <= 0:
                return None
            return {
                "x": max(0.0, min(1.0, x / image_w)),
                "y": max(0.0, min(1.0, y / image_h)),
                "w": max(0.01, min(1.0, w / image_w)),
                "h": max(0.01, min(1.0, h / image_h)),
                "source": bbox.get("source") or "auto",
            }
        except Exception as exc:
            self.task.setdefault("logs", []).append(f"selection parse failed: {exc}")
            return None

    def _terminate_process_tree(self, proc):
        if not proc or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _run_process(self, command, timeout_seconds):
        env = base_env()
        timed_out = False
        try:
            with subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            ) as proc:
                with self.lock:
                    self.process = proc

                lines = queue.Queue()

                def read_output():
                    try:
                        for line in proc.stdout or []:
                            lines.put(line)
                    finally:
                        lines.put(None)

                reader = threading.Thread(target=read_output, name="task-output-reader", daemon=True)
                reader.start()
                start_t = time.monotonic()
                reader_done = False

                while proc.poll() is None:
                    while True:
                        try:
                            item = lines.get_nowait()
                        except queue.Empty:
                            break
                        if item is None:
                            reader_done = True
                        else:
                            self._append_log(item)

                    if timeout_seconds and time.monotonic() - start_t > timeout_seconds:
                        timed_out = True
                        self._append_log(f"⏱️ 任务超过 {timeout_seconds}s 未完成，已自动终止。")
                        self._terminate_process_tree(proc)
                        break

                    time.sleep(0.1)

                try:
                    returncode = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._terminate_process_tree(proc)
                    returncode = -9

                drain_deadline = time.monotonic() + 2.0
                while time.monotonic() < drain_deadline and not reader_done:
                    try:
                        item = lines.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if item is None:
                        reader_done = True
                    else:
                        self._append_log(item)

            with self.lock:
                if self.task:
                    self.task["running"] = False
                    self.task["timed_out"] = timed_out
                    self.task["returncode"] = -9 if timed_out else returncode
                    self.task["finished_at"] = now_ms()
                    if timed_out:
                        self.task["error"] = f"任务超过 {timeout_seconds}s 自动终止"
                    if self.task.get("action") in {"scan", "identify_selection"} and returncode == 0 and not timed_out:
                        pending = self._load_pending_from_task_unlocked()
                        if pending:
                            self.pending_item = pending
        except Exception as exc:
            with self.lock:
                if self.task:
                    self.task["running"] = False
                    self.task["returncode"] = -1
                    self.task["finished_at"] = now_ms()
                    self.task["error"] = str(exc)
        finally:
            with self.lock:
                self.process = None

    def _load_pending_from_task_unlocked(self):
        if not self.task:
            return None
        result_name = self.task.get("latest_result")
        if not result_name and self.task.get("latest_image"):
            result_name = Path(self.task["latest_image"]).with_suffix(".json").name
        if not result_name:
            return None

        result_path = (LOG_DIR / result_name).resolve()
        if not str(result_path).startswith(str(LOG_DIR.resolve())) or not result_path.exists():
            return None

        return self._pending_from_result_path(result_path, latest_image=self.task.get("latest_image"))

    def _pending_from_result_path(self, result_path: Path, latest_image: str | None = None):
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            if self.task:
                self.task.setdefault("logs", []).append(f"pending result parse failed: {exc}")
            return None

        image_path = Path(data.get("image") or "")
        if not image_path.exists() and latest_image:
            image_path = LOG_DIR / latest_image

        ai = data.get("ai") or {}
        measurement = data.get("measurement") or {}
        source_image_path = Path(measurement.get("source_image") or "")
        if not source_image_path.exists():
            source_image_path = image_path
        return {
            "id": result_path.stem,
            "result_name": result_path.name,
            "result_path": str(result_path),
            "image_name": image_path.name if image_path else None,
            "image_path": str(image_path) if image_path else None,
            "source_image_name": source_image_path.name if source_image_path else None,
            "source_image_path": str(source_image_path) if source_image_path else None,
            "aux_image_name": Path(measurement.get("aux_image") or "").name if measurement.get("aux_image") else None,
            "aux_image_path": measurement.get("aux_image"),
            "ai": ai,
            "measurement": measurement,
            "editable": {
                "name": ai.get("name") or "",
                "category": ai.get("category") or "",
                "manufacturer": ai.get("manufacturer") or "",
                "model": ai.get("model") or "",
                "asset_code": canonical_asset_code(ai.get("assetId") or ai.get("asset_id") or ai.get("code")),
                "quantity": ai.get("quantity") or 1,
                "tags": ", ".join([str(t) for t in (ai.get("tags") or [])]),
                "suggested_location": ai.get("suggested_location") or "",
                "description": ai.get("description") or "",
                "reasoning": ai.get("reasoning") or "",
                "size": ai.get("size") or "",
                "width_mm": measurement.get("width_mm"),
                "height_mm": measurement.get("height_mm"),
                "weight_g": measurement.get("weight_g"),
            },
            "created_at": now_ms(),
        }

    def load_pending_from_result(self, result_name: str):
        name = Path(str(result_name or "")).name
        if not name:
            return False, "缺少结果文件名"
        result_path = (LOG_DIR / name).resolve()
        if not str(result_path).startswith(str(LOG_DIR.resolve())) or not result_path.exists():
            return False, "结果文件不存在"
        pending = self._pending_from_result_path(result_path)
        if not pending:
            return False, "结果文件无法载入"
        with self.lock:
            self.pending_item = pending
        return True, f"已载入待确认物品 {pending['id']}"

    def discard_pending(self):
        with self.lock:
            self.pending_item = None
        return True, "已清除待确认物品"

    def commit_pending(self, edited: dict):
        with self.lock:
            pending = json.loads(json.dumps(self.pending_item, ensure_ascii=False)) if self.pending_item else None
        if not pending:
            return False, "没有待确认物品", None

        ai = pending.get("ai") or {}
        editable = pending.get("editable") or {}
        fields = {**editable, **(edited or {})}
        asset_code_input = str(
            fields.get("asset_code")
            or fields.get("assetId")
            or fields.get("asset_id")
            or ""
        ).strip()
        asset_code = canonical_asset_code(asset_code_input)
        if asset_code_input and not asset_code:
            return False, "资产编号需为 10 位数字，例如 2607060001；可粘贴 ORB-2607060001，保存时会自动去掉 ORB-。", None
        tags = fields.get("tags") or []
        if isinstance(tags, str):
            tags = [tag.strip() for tag in re.split(r"[,，、/;；\n]+", tags) if tag.strip()]
        tags = list(dict.fromkeys([str(tag).strip() for tag in tags if str(tag).strip()]))

        ai.update(
            {
                "name": str(fields.get("name") or "未命名物品").strip(),
                "category": str(fields.get("category") or "").strip(),
                "manufacturer": str(fields.get("manufacturer") or "").strip() or None,
                "model": str(fields.get("model") or "").strip() or None,
                "quantity": int(fields.get("quantity") or 1),
                "tags": tags,
                "suggested_location": str(fields.get("suggested_location") or "").strip() or None,
                "description": str(fields.get("description") or "").strip(),
                "reasoning": str(fields.get("reasoning") or "").strip(),
                "size": str(fields.get("size") or "").strip() or None,
            }
        )
        if asset_code:
            ai["assetId"] = asset_code
            fields["asset_code"] = asset_code

        measurement = pending.get("measurement") or {}
        for key in ("width_mm", "height_mm", "weight_g"):
            if fields.get(key) not in (None, ""):
                try:
                    measurement[key] = float(fields[key])
                except Exception:
                    pass

        image = None
        image_path = Path(pending.get("image_path") or "")
        if image_path.is_file():
            image = cv2.imread(str(image_path))

        try:
            client = runtime_homebox_client()
            if not client.authenticated:
                return False, "缺少 Homebox 登录信息：请设置 HOMEBOX_TOKEN 或 HOMEBOX_USERNAME/HOMEBOX_PASSWORD", None
            location_name = str(ai.get("suggested_location") or "").strip()
            allow_create_location = bool(fields.get("create_missing_location"))
            try:
                location_id = client.resolve_location_id_exact(location_name)
            except HomeboxError as exc:
                location_id = None
                with self.lock:
                    if self.task:
                        self.task.setdefault("logs", []).append(f"Homebox location exact match skipped: {exc}")
            if location_name and not location_id:
                if not allow_create_location:
                    return False, f"Homebox 位置不存在: {location_name}。请选择已有位置，或确认创建新位置后再入库。", {
                        "missing_location": location_name,
                    }
                location = client.create_location(location_name, description="Created by O.R.B.I.T.")
                location_id = location.get("id") if isinstance(location, dict) else None
                if not location_id:
                    return False, f"Homebox 新位置创建失败: {location_name}", None
                with self.lock:
                    if self.task:
                        self.task.setdefault("logs", []).append(f"Homebox location created: {location_name} ({location_id})")
            item = client.create_item(
                ai,
                image_cv2=image,
                weight_g=measurement.get("weight_g"),
                dimensions=measurement,
                location_id=location_id,
                dry_run=False,
            )
        except HomeboxError as exc:
            return False, f"Homebox 入库失败: {exc}", None
        except Exception as exc:
            return False, f"入库失败: {exc}", None

        stored_item = item if isinstance(item, dict) else {"id": str(item), "name": ai.get("name")}
        stored_code = canonical_asset_code(stored_item.get("assetId") or stored_item.get("asset_id") or asset_code)
        if stored_code:
            stored_item["assetId"] = stored_code
            stored_item["asset_id"] = stored_code
            ai["assetId"] = stored_code
            fields["asset_code"] = stored_code
        with self.lock:
            if self.pending_item and self.pending_item.get("id") == pending.get("id"):
                self.pending_item["ai"] = ai
                self.pending_item["measurement"] = measurement
                self.pending_item["editable"] = fields
                self.pending_item["committed_item"] = stored_item
                self.pending_item["committed_at"] = now_ms()
            if self.task:
                self.task.setdefault("logs", []).append(f"Homebox item committed: {stored_item.get('id')}")
        record_ok, record_message, _record_meta = self.save_intake_record_for_pending("homebox_commit")
        return True, f"入库完成，可继续写标签；{record_message}", stored_item

    def _ensure_item_asset_id(
        self,
        item: dict,
        *,
        asset_code: str | None = None,
        sync_homebox: bool = False,
        pending_id: str | None = None,
    ) -> dict:
        item = dict(item or {})
        code = canonical_asset_code(asset_code) if asset_code else ""
        if not code:
            code = rfid_epc_code(item)
        current = str(item.get("assetId") or item.get("asset_id") or "").strip()
        if canonical_asset_code(current) == code:
            return item

        item["assetId"] = code
        item["asset_id"] = code

        if sync_homebox:
            item_id = str(item.get("id") or "").strip()
            if item_id and item_id != "dry-run":
                try:
                    client = runtime_homebox_client(timeout=12)
                    if client.authenticated:
                        updated = client.set_item_asset_id(item_id, code)
                        if isinstance(updated, dict):
                            updated.setdefault("assetId", code)
                            updated.setdefault("asset_id", code)
                            item.update(updated)
                except Exception as exc:
                    with self.lock:
                        if self.task:
                            self.task.setdefault("logs", []).append(f"Homebox asset ID sync failed: {exc}")

        if pending_id:
            with self.lock:
                if self.pending_item and self.pending_item.get("id") == pending_id:
                    self.pending_item["committed_item"] = item
        return item

    def write_labels_for_pending(self, options: dict | None = None):
        with self.lock:
            pending = json.loads(json.dumps(self.pending_item, ensure_ascii=False)) if self.pending_item else None
        if not pending:
            return False, "没有待处理物品", None
        item = pending.get("committed_item")
        if not item:
            return False, "请先入库，再写标签", None

        ai = pending.get("ai") or {}
        measurement = pending.get("measurement") or {}
        config = config_snapshot()
        item = self._ensure_item_asset_id(
            item,
            asset_code=(pending.get("editable") or {}).get("asset_code"),
            sync_homebox=True,
            pending_id=pending.get("id"),
        )
        label_result = None
        rfid_result = None
        errors = []
        options = options or {}
        pet_enabled = config_bool(options.get("print_pet_labels"), config.get("print_pet_labels", "1")) == "1"
        rfid_enabled = config_bool(options.get("write_rfid_tags"), config.get("write_rfid_tags", "1")) == "1"
        rfid_target_epc = str(options.get("rfid_target_epc") or "").strip()
        if not pet_enabled and not rfid_enabled:
            return False, "请至少选择 PET 标签或 RFID 标签", None
        if rfid_enabled and not rfid_target_epc:
            return False, "缺少 RFID 目标标签，请先盘点并确认要写入的标签", None

        if pet_enabled:
            try:
                label_result = print_item_label_set(
                    item,
                    ai=ai,
                    measurement=measurement,
                    homebox_url=config.get("homebox_url", DEFAULT_HOMEBOX_URL),
                    printer_name=os.getenv("ORBIT_LABEL_PRINTER", "TSC TTP-244 Pro"),
                    dry_run=os.getenv("ORBIT_PRINT_LABELS_DRY_RUN", "0") != "0",
                )
            except Exception as exc:
                label_result = {"printed": False, "error": str(exc)}
                errors.append(f"PET 标签失败: {exc}")

        if rfid_enabled:
            payload = rfid_payload(item, config.get("homebox_url", DEFAULT_HOMEBOX_URL))
            try:
                port = config.get("rfid_port") or os.getenv("RFID_PORT", "COM3")
                baud = int(os.getenv("RFID_BAUD", "115200"))
                with E710Reader(port=port, baudrate=baud) as reader:
                    rfid_result = reader.write_epc_hex(
                        payload["epc_hex_candidate"],
                        require_single=False,
                        expected_current_epc=rfid_target_epc,
                    )
                rfid_result["payload"] = payload
                rfid_result["selected_epc"] = rfid_target_epc
            except Exception as exc:
                rfid_result = {"written": False, "payload": payload, "error": str(exc)}
                errors.append(f"RFID 写入失败: {exc}")

        if not label_result and not rfid_result:
            return False, "PET 标签和 RFID 写入都未启用", None

        result = {"label": label_result, "rfid": rfid_result}
        with self.lock:
            if self.pending_item and self.pending_item.get("id") == pending.get("id"):
                self.pending_item["label_result"] = label_result
                self.pending_item["rfid_result"] = rfid_result
                self.pending_item["labeled_at"] = now_ms()
            if self.task:
                self.task.setdefault("logs", []).append(f"label/write result: {json.dumps(result, ensure_ascii=False)}")

        if not errors:
            record_ok, record_message, _record_meta = self.save_intake_record_for_pending("label_write")
            return True, f"写标签完成；{record_message}", result

        if errors:
            return False, "；".join(errors), result
        return True, "写标签完成", result

    def label_preview_for_pending(self):
        with self.lock:
            pending = json.loads(json.dumps(self.pending_item, ensure_ascii=False)) if self.pending_item else None
        if not pending:
            return False, "没有待预览物品", None
        item = pending.get("committed_item")
        if not item:
            return False, "请先入库，再预览标签", None

        ai = pending.get("ai") or {}
        measurement = pending.get("measurement") or {}
        config = config_snapshot()
        item = self._ensure_item_asset_id(item, asset_code=(pending.get("editable") or {}).get("asset_code"), sync_homebox=False)
        try:
            preview = print_item_label_set(
                item,
                ai=ai,
                measurement=measurement,
                homebox_url=config.get("homebox_url", DEFAULT_HOMEBOX_URL),
                printer_name=os.getenv("ORBIT_LABEL_PRINTER", "TSC TTP-244 Pro"),
                dry_run=True,
            )
            for key in ("human_preview", "code_preview"):
                if preview.get(key):
                    preview[f"{key}_name"] = Path(preview[key]).name
        except Exception as exc:
            return False, f"标签预览失败: {exc}", None

        with self.lock:
            if self.pending_item and self.pending_item.get("id") == pending.get("id"):
                self.pending_item["label_preview"] = preview
        return True, "标签预览已生成", preview


    def rfid_payload_for_pending(self):
        with self.lock:
            pending = json.loads(json.dumps(self.pending_item, ensure_ascii=False)) if self.pending_item else None
        if not pending or not pending.get("committed_item"):
            return None
        config = config_snapshot()
        item = self._ensure_item_asset_id(
            pending["committed_item"],
            asset_code=(pending.get("editable") or {}).get("asset_code"),
            sync_homebox=False,
        )
        return rfid_payload(item, config.get("homebox_url", DEFAULT_HOMEBOX_URL))

    def save_intake_record_for_pending(self, reason: str = "manual"):
        with self.lock:
            pending = json.loads(json.dumps(self.pending_item, ensure_ascii=False)) if self.pending_item else None
        if not pending:
            return False, "没有可保存的入库状态", None
        try:
            record, path = self._write_intake_record(pending, reason=reason)
        except Exception as exc:
            return False, f"入库记录保存失败: {exc}", None

        meta = {
            "path": str(path),
            "name": path.name,
            "record_id": record.get("record_id"),
            "schema_version": record.get("schema_version"),
            "saved_at": record.get("created_at"),
            "reason": reason,
        }
        with self.lock:
            if self.pending_item and self.pending_item.get("id") == pending.get("id"):
                self.pending_item["intake_record"] = meta
            if self.task:
                self.task.setdefault("logs", []).append(f"intake record saved: {path}")
        return True, f"入库记录已保存: {path.name}", meta

    def import_intake_record(self, record: dict):
        if not isinstance(record, dict):
            return False, "导入失败：文件不是 JSON 对象", None
        schema = record.get("schema") or ""
        version = int(record.get("schema_version") or 0)
        if schema and schema != INTAKE_RECORD_SCHEMA:
            return False, f"导入失败：不支持的记录类型 {schema}", None
        if version and int(record.get("min_reader_schema_version") or 1) > INTAKE_RECORD_VERSION:
            return False, "导入失败：记录需要更新版本的 O.R.B.I.T. 才能读取", None

        pending = (
            (record.get("gui") or {}).get("pending_item")
            or record.get("pending_item")
            or record.get("payload", {}).get("pending_item")
        )
        if not isinstance(pending, dict):
            return False, "导入失败：记录中没有 pending_item", None

        pending = self._normalize_imported_pending(pending, record)
        with self.lock:
            self._reap_stale_task_locked()
            if self.task and self.task.get("running"):
                return False, "当前还有任务运行中，不能导入记录", None
            self.pending_item = pending
            self.task = {
                "id": str(uuid.uuid4())[:8],
                "action": "import_record",
                "label": "导入入库记录",
                "timeout_seconds": 0,
                "timed_out": False,
                "running": False,
                "returncode": 0,
                "started_at": now_ms(),
                "finished_at": now_ms(),
                "logs": [f"imported intake record: {record.get('record_id') or pending.get('id')}"],
                "latest_image": pending.get("source_image_name") or pending.get("image_name"),
                "latest_aux_image": pending.get("aux_image_name"),
                "latest_result": pending.get("result_name"),
                "latest_selection": self._selection_from_pending(pending),
                "request_selection": None,
                "error": None,
            }
        return True, f"已导入入库记录: {pending.get('editable', {}).get('name') or pending.get('id')}", pending

    def _write_intake_record(self, pending: dict, reason: str):
        INTAKE_RECORD_DIR.mkdir(parents=True, exist_ok=True)
        record = self._build_intake_record(pending, reason=reason)
        summary = record.get("summary") or {}
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name = self._safe_filename(summary.get("name") or "item")[:40]
        key = self._safe_filename(summary.get("code") or summary.get("item_id") or record["record_id"])[:48]
        path = INTAKE_RECORD_DIR / f"{stamp}_{name}_{key}.orbit-intake.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record, path

    def _build_intake_record(self, pending: dict, reason: str):
        pending = json.loads(json.dumps(pending, ensure_ascii=False))
        config = config_snapshot()
        item = pending.get("committed_item") or {}
        ai = pending.get("ai") or {}
        editable = pending.get("editable") or {}
        name = editable.get("name") or ai.get("name") or item.get("name") or pending.get("id") or "item"
        item_id = str(item.get("id") or "")
        try:
            code = rfid_epc_code(item) if item else ""
        except Exception:
            code = str(item.get("assetId") or item.get("asset_id") or "")
        display = display_code(item) if item and code else code
        summary = {
            "name": str(name),
            "item_id": item_id,
            "code": code,
            "display_code": display,
            "rfid_epc": code,
            "homebox_url": config.get("homebox_url", DEFAULT_HOMEBOX_URL),
            "item_url": item_url(config.get("homebox_url", DEFAULT_HOMEBOX_URL), item) if item else "",
            "committed_at": pending.get("committed_at"),
            "labeled_at": pending.get("labeled_at"),
        }
        payloads = {}
        if item:
            try:
                payloads["rfid"] = rfid_payload(item, config.get("homebox_url", DEFAULT_HOMEBOX_URL))
            except Exception:
                payloads["rfid"] = None
        return {
            "schema": INTAKE_RECORD_SCHEMA,
            "schema_version": INTAKE_RECORD_VERSION,
            "min_reader_schema_version": 1,
            "record_id": str(uuid.uuid4()),
            "created_at": now_ms(),
            "reason": reason,
            "summary": summary,
            "gui": {
                "config_public": public_config(config),
                "pending_item": pending,
                "task": self._public_task_snapshot_for_record(),
                "scale": scale_monitor.snapshot() if "scale_monitor" in globals() else None,
                "system": status_probe.snapshot() if "status_probe" in globals() else None,
            },
            "payloads": payloads,
            "artifacts": self._artifact_manifest(pending),
        }

    def _public_task_snapshot_for_record(self):
        with self.lock:
            if not self.task:
                return None
            task = json.loads(json.dumps(self.task, ensure_ascii=False))
        task.pop("pending_item", None)
        task["logs"] = list(task.get("logs") or [])[-120:]
        return task

    def _artifact_manifest(self, pending: dict):
        artifacts = {}
        for key in ("result_path", "image_path", "source_image_path", "aux_image_path"):
            meta = self._file_meta(pending.get(key))
            if meta:
                artifacts[key] = meta
        for group_name in ("label_preview", "label_result"):
            group = pending.get(group_name) if isinstance(pending.get(group_name), dict) else {}
            for key in ("human_preview", "code_preview", "human_label", "code_label"):
                meta = self._file_meta(group.get(key))
                if meta:
                    artifacts[f"{group_name}.{key}"] = meta
        return artifacts

    @staticmethod
    def _file_meta(path_value):
        if not path_value:
            return None
        path = Path(str(path_value))
        meta = {"path": str(path), "name": path.name}
        try:
            if path.is_file():
                stat = path.stat()
                meta.update({"exists": True, "size": stat.st_size, "mtime": int(stat.st_mtime * 1000)})
            else:
                meta["exists"] = False
        except Exception:
            meta["exists"] = False
        return meta

    @staticmethod
    def _safe_filename(value):
        text = re.sub(r'[\\/:*?"<>|\s]+', "_", str(value or "").strip()).strip("._")
        return text or "item"

    def _normalize_imported_pending(self, pending: dict, record: dict):
        pending = json.loads(json.dumps(pending, ensure_ascii=False))
        pending.setdefault("id", (record.get("summary") or {}).get("item_id") or record.get("record_id") or str(uuid.uuid4())[:8])
        ai = pending.setdefault("ai", {})
        measurement = pending.setdefault("measurement", {})
        editable = pending.get("editable")
        if not isinstance(editable, dict):
            editable = {
                "name": ai.get("name") or "",
                "category": ai.get("category") or "",
                "manufacturer": ai.get("manufacturer") or "",
                "model": ai.get("model") or "",
                "asset_code": "",
                "quantity": ai.get("quantity") or 1,
                "tags": ", ".join([str(t) for t in (ai.get("tags") or [])]) if isinstance(ai.get("tags"), list) else str(ai.get("tags") or ""),
                "suggested_location": ai.get("suggested_location") or "",
                "description": ai.get("description") or "",
                "reasoning": ai.get("reasoning") or "",
                "size": ai.get("size") or "",
                "width_mm": measurement.get("width_mm"),
                "height_mm": measurement.get("height_mm"),
                "weight_g": measurement.get("weight_g"),
            }
            pending["editable"] = editable
        item = pending.get("committed_item") if isinstance(pending.get("committed_item"), dict) else {}
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        asset_code = canonical_asset_code(
            editable.get("asset_code")
            or editable.get("assetId")
            or editable.get("asset_id")
            or summary.get("code")
            or summary.get("rfid_epc")
            or item.get("assetId")
            or item.get("asset_id")
            or ai.get("assetId")
            or ai.get("asset_id")
            or ai.get("code")
        )
        if asset_code:
            editable["asset_code"] = asset_code
            ai["assetId"] = asset_code
            if item:
                item["assetId"] = asset_code
                item["asset_id"] = asset_code
        pending["imported_record"] = {
            "schema": record.get("schema") or INTAKE_RECORD_SCHEMA,
            "schema_version": record.get("schema_version") or 0,
            "record_id": record.get("record_id"),
            "imported_at": now_ms(),
            "summary": record.get("summary") or {},
        }
        self._restore_imported_artifact_names(pending, record)
        return pending

    def _restore_imported_artifact_names(self, pending: dict, record: dict):
        artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
        for pending_key in ("source_image_path", "image_path", "aux_image_path", "result_path"):
            path_value = pending.get(pending_key)
            if not path_value:
                meta = artifacts.get(pending_key) or {}
                path_value = meta.get("path")
            restored = self._restore_path_for_gui(path_value)
            if restored:
                pending[pending_key] = restored["path"]
                if pending_key == "source_image_path":
                    pending["source_image_name"] = restored["name"]
                elif pending_key == "image_path":
                    pending["image_name"] = restored["name"]
                elif pending_key == "aux_image_path":
                    pending["aux_image_name"] = restored["name"]
                elif pending_key == "result_path":
                    pending["result_name"] = restored["name"]

    @staticmethod
    def _restore_path_for_gui(path_value):
        if not path_value:
            return None
        path = Path(str(path_value))
        if not path.is_file():
            return None
        try:
            if path.resolve().parent == LOG_DIR.resolve():
                return {"path": str(path.resolve()), "name": path.name}
        except Exception:
            pass
        try:
            LOG_DIR.mkdir(exist_ok=True)
            target = LOG_DIR / f"restored_{int(time.time())}_{path.name}"
            shutil.copy2(path, target)
            return {"path": str(target), "name": target.name}
        except Exception:
            return None

    def _selection_from_pending(self, pending: dict):
        bbox = (pending.get("measurement") or {}).get("selection_bbox")
        if not isinstance(bbox, dict):
            return None
        try:
            x = float(bbox.get("x") or 0)
            y = float(bbox.get("y") or 0)
            w = float(bbox.get("w") or 1)
            h = float(bbox.get("h") or 1)
            image_w = float(bbox.get("image_w") or 0)
            image_h = float(bbox.get("image_h") or 0)
            if x > 1 or y > 1 or w > 1 or h > 1:
                if image_w <= 0 or image_h <= 0:
                    return None
                x, y, w, h = x / image_w, y / image_h, w / image_w, h / image_h
            return {
                "x": max(0.0, min(0.99, x)),
                "y": max(0.0, min(0.99, y)),
                "w": max(0.01, min(1.0 - max(0.0, min(0.99, x)), w)),
                "h": max(0.01, min(1.0 - max(0.0, min(0.99, y)), h)),
                "source": bbox.get("source") or "imported",
            }
        except Exception:
            return None


class StatusProbe:
    def __init__(self):
        self.lock = threading.Lock()
        config = config_snapshot()
        self.status = {
            "updated_at": None,
            "serial_ports": [],
            "cameras": {"aux_enabled": True, "aux_detected": False, "message": "未检测", "devices": []},
            "realsense": {"connected": False, "devices": [], "error": None},
            "homebox": {"connected": False, "url": config["homebox_url"], "error": None},
            "ollama": {"connected": False, "model": config["ollama_model"], "models": [], "ps": "", "error": None},
            "rfid": {"available": False, "state": "unknown", "message": "未检测", "devices": []},
        }
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, name="status-probe", daemon=True).start()

    def stop(self):
        self.running = False

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.status, ensure_ascii=False))

    def _loop(self):
        while self.running:
            status = self._collect()
            with self.lock:
                self.status = status
            time.sleep(5.0)

    def _collect(self):
        serial_ports = self._serial_ports()
        return {
            "updated_at": now_ms(),
            "serial_ports": serial_ports,
            "cameras": self._cameras(),
            "realsense": self._realsense(),
            "homebox": self._homebox(),
            "ollama": self._ollama(),
            "rfid": self._rfid(serial_ports),
        }

    def _serial_ports(self):
        try:
            return ElectronicScaleReader.available_ports()
        except Exception as exc:
            return [{"device": "error", "description": str(exc), "hwid": ""}]

    def _realsense(self):
        if rs is None:
            return {"connected": False, "devices": [], "error": "pyrealsense2 不可用"}
        try:
            devices = []
            for dev in rs.context().query_devices():
                devices.append(
                    {
                        "name": dev.get_info(rs.camera_info.name),
                        "serial": dev.get_info(rs.camera_info.serial_number),
                    }
                )
            return {"connected": bool(devices), "devices": devices, "error": None}
        except Exception as exc:
            return {"connected": False, "devices": [], "error": str(exc)}

    def _cameras(self):
        config = config_snapshot()
        devices = self._camera_pnp_devices()
        aux_candidate = next(
            (
                dev
                for dev in devices
                if re.search(r"Camera|Webcam|Logitech|USB Video|C270", str(dev.get("Name") or ""), re.I)
                and not re.search(r"RealSense|Depth|Audio|Microphone|麦克风", str(dev.get("Name") or ""), re.I)
            ),
            None,
        )
        return {
            "aux_enabled": config_bool(config.get("aux_camera_enabled"), "1") == "1",
            "aux_index": config.get("aux_camera_index", "0"),
            "aux_backend": config.get("aux_camera_backend", "dshow"),
            "aux_width": config.get("aux_camera_width", "1280"),
            "aux_height": config.get("aux_camera_height", "720"),
            "aux_auto_exposure": config_bool(config.get("aux_camera_auto_exposure"), "1") == "1",
            "aux_detected": aux_candidate is not None,
            "message": "检测到辅助相机候选" if aux_candidate else "未在 PnP 里识别到辅助相机候选",
            "devices": devices,
        }

    def _camera_pnp_devices(self):
        command = (
            "Get-CimInstance Win32_PnPEntity | "
            "Where-Object { $_.Name -match 'Camera|Webcam|C270|Logitech|USB Video|RealSense|Imaging' -or $_.Service -match 'usbvideo' } | "
            "Select-Object Name,PNPDeviceID,Status,ConfigManagerErrorCode,Service | "
            "ConvertTo-Json -Compress"
        )
        try:
            raw = run_powershell_text(command, timeout=5)
            if not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else [data]
        except Exception:
            return []

    def _homebox(self):
        url = config_snapshot().get("homebox_url", DEFAULT_HOMEBOX_URL).rstrip("/")
        try:
            response = requests.get(f"{url}/api/v1/status", timeout=3)
            response.raise_for_status()
            data = response.json()
            build = data.get("build") or {}
            return {
                "connected": True,
                "url": url,
                "title": data.get("title"),
                "version": build.get("version"),
                "error": None,
            }
        except Exception as exc:
            return {"connected": False, "url": url, "error": str(exc)}

    def _ollama(self):
        config = config_snapshot()
        ai_target = str(config.get("ai_target") or DEFAULT_AI_TARGET).strip().lower()
        cloud_provider = str(config.get("cloud_provider") or DEFAULT_CLOUD_PROVIDER).strip().lower()
        if ai_target == "cloud":
            cloud = list_openai_compatible_models(
                cloud_provider,
                config.get("ai_api_base") or default_cloud_base(cloud_provider),
                config.get("ai_api_key") or "",
                timeout=3,
            )
            return {
                "connected": bool(cloud.get("ok")),
                "provider": cloud_provider,
                "base_url": cloud.get("base_url"),
                "model": config.get("ollama_model") or DEFAULT_MODEL,
                "models": cloud.get("models") or [],
                "ps": cloud.get("ps") or "",
                "error": None if cloud.get("ok") else cloud.get("message"),
            }
        api = normalize_ollama_base_url(config.get("ollama_url") or DEFAULT_OLLAMA_URL)
        result = {"connected": False, "provider": "ollama", "base_url": api, "model": config.get("ollama_model") or DEFAULT_MODEL, "models": [], "ps": "", "error": None}
        try:
            response = requests.get(f"{api}/api/tags", timeout=3)
            response.raise_for_status()
            models = response.json().get("models") or []
            result["models"] = [m.get("name") for m in models if m.get("name")]
            result["connected"] = True
        except Exception as exc:
            result["error"] = str(exc)

        try:
            ps = requests.get(f"{api}/api/ps", timeout=3)
            if ps.ok:
                rows = ps.json().get("models") or []
                result["ps"] = "\n".join(
                    f"{row.get('name','')} {row.get('size_vram') or row.get('size') or ''} {row.get('processor','')}"
                    for row in rows
                ) or "当前未加载模型"
        except Exception as exc:
            result["ps"] = f"ollama ps failed: {exc}"
        return result

    def _rfid(self, serial_ports):
        config = config_snapshot()
        scale_port = str(config.get("scale_port") or os.getenv("SCALE_PORT", "COM9")).upper()
        configured_rfid = str(config.get("rfid_port") or "").upper()
        candidates = []
        for port in serial_ports:
            text = f"{port.get('device','')} {port.get('description','')} {port.get('hwid','')}"
            if port.get("device", "").upper() == scale_port:
                continue
            if configured_rfid and port.get("device", "").upper() == configured_rfid:
                candidates.insert(0, port)
            elif re.search(r"RFID|UHF|CP210|CH340|CH341|Prolific|UART", text, re.I):
                candidates.append(port)

        pnp_devices = self._rfid_pnp_devices()
        if candidates:
            return {
                "available": True,
                "state": "serial_ready",
                "message": f"发现可用串口 {candidates[0]['device']}",
                "ports": candidates,
                "devices": pnp_devices,
            }

        cp210_problem = next(
            (
                dev
                for dev in pnp_devices
                if "CP210" in str(dev.get("Name", dev.get("FriendlyName", ""))).upper()
                and int(dev.get("ConfigManagerErrorCode") or 0) != 0
            ),
            None,
        )
        if cp210_problem:
            return {
                "available": False,
                "state": "driver_error",
                "message": "检测到 CP2102，但驱动未安装或设备未正确启动，未生成 COM 口",
                "ports": [],
                "devices": pnp_devices,
            }

        return {
            "available": False,
            "state": "missing",
            "message": "未检测到可用 RFID 串口",
            "ports": [],
            "devices": pnp_devices,
        }

    def _rfid_pnp_devices(self):
        command = (
            "Get-CimInstance Win32_PnPEntity | "
            "Where-Object { $_.Name -match 'CP210|RFID|UHF|CH340|CH341|UART|USB to UART' } | "
            "Select-Object Name,PNPDeviceID,Status,ConfigManagerErrorCode,Service | "
            "ConvertTo-Json -Compress"
        )
        try:
            raw = run_powershell_text(command, timeout=5)
            if not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else [data]
        except Exception:
            return []


scale_monitor = ScaleMonitor()
status_probe = StatusProbe()
task_runner = TaskRunner()


class OrbitHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class OrbitHandler(SimpleHTTPRequestHandler):
    server_version = "OrbitWeb/0.1"

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            return self._json(self._status_payload())
        if path == "/api/task":
            return self._json(task_runner.snapshot())
        if path == "/api/ollama/models":
            return self._json(self._ollama_models(parsed))
        if path == "/api/homebox/options":
            return self._json(self._homebox_options())
        if path == "/api/events":
            return self._events()
        if path.startswith("/logs/"):
            return self._serve_log_file(path)
        return self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        payload = self._read_json()
        if parsed.path == "/api/action":
            config, scale_changed = update_runtime_config(payload)
            if scale_changed:
                scale_monitor.reconfigure()
            ok, message = task_runner.start(payload.get("action", ""), payload, scale_monitor.snapshot())
            return self._json({"ok": ok, "message": message, "config": public_config(config), "task": task_runner.snapshot()}, 200 if ok else 409)
        if parsed.path == "/api/config":
            config, scale_changed = update_runtime_config(payload)
            if scale_changed:
                scale_monitor.reconfigure()
            return self._json({"ok": True, "config": public_config(config), "scale_reconfigured": scale_changed})
        if parsed.path == "/api/ai/models":
            return self._json(self._ai_models(payload))
        if parsed.path == "/api/cancel":
            ok, message = task_runner.cancel()
            return self._json({"ok": ok, "message": message, "task": task_runner.snapshot()}, 200 if ok else 409)
        if parsed.path == "/api/commit":
            ok, message, item = task_runner.commit_pending(payload)
            return self._json(
                {"ok": ok, "message": message, "item": item, "task": task_runner.snapshot()},
                200 if ok else 409,
            )
        if parsed.path == "/api/write_labels":
            ok, message, result = task_runner.write_labels_for_pending(payload)
            return self._json(
                {"ok": ok, "message": message, "result": result, "task": task_runner.snapshot()},
                200 if ok else 409,
            )
        if parsed.path == "/api/label_preview":
            ok, message, preview = task_runner.label_preview_for_pending()
            return self._json(
                {"ok": ok, "message": message, "preview": preview, "task": task_runner.snapshot()},
                200 if ok else 409,
            )
        if parsed.path == "/api/save_intake_record":
            ok, message, record = task_runner.save_intake_record_for_pending(payload.get("reason") or "manual")
            return self._json(
                {"ok": ok, "message": message, "record": record, "task": task_runner.snapshot()},
                200 if ok else 409,
            )
        if parsed.path == "/api/import_intake_record":
            ok, message, pending = task_runner.import_intake_record(payload.get("record") or payload)
            return self._json(
                {"ok": ok, "message": message, "pending_item": pending, "task": task_runner.snapshot()},
                200 if ok else 409,
            )
        if parsed.path == "/api/find/search":
            return self._json(self._find_search(payload))
        if parsed.path == "/api/find/item":
            return self._json(self._find_item(payload))
        if parsed.path == "/api/find/rfid":
            config, _scale_changed = update_runtime_config(payload)
            return self._json(self._find_rfid(config))
        if parsed.path == "/api/discard_pending":
            ok, message = task_runner.discard_pending()
            return self._json({"ok": ok, "message": message, "task": task_runner.snapshot()})
        if parsed.path == "/api/load_pending":
            ok, message = task_runner.load_pending_from_result(payload.get("result_name", ""))
            return self._json({"ok": ok, "message": message, "task": task_runner.snapshot()}, 200 if ok else 409)
        if parsed.path == "/api/rfid/probe":
            config, _scale_changed = update_runtime_config(payload)
            return self._json(self._rfid_probe(config))
        if parsed.path == "/api/rfid/inventory":
            config, _scale_changed = update_runtime_config(payload)
            return self._json(self._rfid_inventory(config))
        if parsed.path == "/api/rfid/release":
            config, _scale_changed = update_runtime_config(payload)
            return self._json(self._rfid_release(config))
        if parsed.path == "/api/camera/probe":
            config, _scale_changed = update_runtime_config(payload)
            return self._json(self._camera_probe(config))
        return self._json({"ok": False, "message": "not found"}, 404)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _json(self, data, status=200):
        body = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        while True:
            try:
                data = json_dumps(self._status_payload()).decode("utf-8")
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(1.0)
            except Exception:
                break

    def _status_payload(self):
        config = config_snapshot()
        return {
            "time": now_ms(),
            "config": public_config(config),
            "scale": scale_monitor.snapshot(),
            "system": status_probe.snapshot(),
            "task": task_runner.snapshot(),
        }

    def _homebox_options(self):
        try:
            client = runtime_homebox_client(timeout=8)
            if not client.authenticated:
                return {
                    "ok": False,
                    "message": "缺少 Homebox 登录信息，无法读取已有标签和位置",
                    "tags": [],
                    "locations": [],
                }
            return {
                "ok": True,
                "tags": self._homebox_name_options(client.get_tags()),
                "locations": self._homebox_name_options(client.get_locations()),
            }
        except HomeboxError as exc:
            return {"ok": False, "message": f"Homebox 读取失败: {exc}", "tags": [], "locations": []}
        except Exception as exc:
            return {"ok": False, "message": f"Homebox 读取异常: {exc}", "tags": [], "locations": []}

    def _find_search(self, payload: dict):
        query = str(payload.get("q") or "").strip()
        limit = max(5, min(100, int(payload.get("limit") or 40)))
        try:
            client = runtime_homebox_client(timeout=12)
            if not client.authenticated:
                return {"ok": False, "message": "缺少 Homebox 登录信息，无法搜索物品", "items": []}

            primary = self._homebox_items_from_response(client.list_items(query or None, page_size=limit))
            combined = {str(item.get("id") or idx): item for idx, item in enumerate(primary)}
            if query:
                broad = self._homebox_items_from_response(client.list_items(None, page_size=max(limit, 100)))
                for item in broad:
                    if self._item_matches_query(item, query):
                        combined[str(item.get("id") or len(combined))] = item
            items = [self._summarize_homebox_item(item) for item in combined.values()]
            if query:
                items.sort(key=lambda item: self._find_score(item, query), reverse=True)
            return {
                "ok": True,
                "message": f"找到 {len(items[:limit])} 个物品" if query else f"读取 {len(items[:limit])} 个物品",
                "items": items[:limit],
            }
        except HomeboxError as exc:
            return {"ok": False, "message": f"Homebox 搜索失败: {exc}", "items": []}
        except Exception as exc:
            return {"ok": False, "message": f"找物搜索异常: {exc}", "items": []}

    def _find_item(self, payload: dict):
        item_id = str(payload.get("id") or "").strip()
        if not item_id:
            return {"ok": False, "message": "缺少物品 ID"}
        try:
            client = runtime_homebox_client(timeout=12)
            if not client.authenticated:
                return {"ok": False, "message": "缺少 Homebox 登录信息，无法读取物品"}
            item = client.get_item(item_id)
            return {"ok": True, "item": self._summarize_homebox_item(item, detail=True)}
        except HomeboxError as exc:
            return {"ok": False, "message": f"Homebox 读取失败: {exc}"}
        except Exception as exc:
            return {"ok": False, "message": f"物品读取异常: {exc}"}

    def _find_rfid(self, config: dict):
        inventory = self._rfid_inventory(config)
        if not inventory.get("ok"):
            return {"ok": False, "message": inventory.get("message") or "RFID 盘点失败", "tags": []}
        try:
            client = runtime_homebox_client(timeout=12)
            items = self._homebox_items_from_response(client.list_items(None, page_size=200)) if client.authenticated else []
        except Exception:
            items = []

        by_code = {}
        by_hex = {}
        for item in items:
            summary = self._summarize_homebox_item(item)
            by_code[summary["rfid_code"].upper()] = summary
            by_hex[rfid_epc_hex(item).upper()] = summary

        matched = []
        for tag in inventory.get("tags") or []:
            tag_epc = str(tag.get("epc") or "").upper()
            tag_ascii = str(tag.get("epc_ascii") or "").strip().upper()
            item = by_hex.get(tag_epc) or by_code.get(tag_ascii)
            row = dict(tag)
            row["item"] = item
            row["matched"] = item is not None
            matched.append(row)
        return {
            "ok": True,
            "message": f"{inventory.get('port')} 读到 {len(matched)} 个 RFID 标签，匹配 {sum(1 for row in matched if row.get('matched'))} 个物品",
            "port": inventory.get("port"),
            "tags": matched,
        }

    @staticmethod
    def _homebox_items_from_response(response):
        if isinstance(response, list):
            return response
        if not isinstance(response, dict):
            return []
        for key in ("items", "data", "results", "rows"):
            value = response.get(key)
            if isinstance(value, list):
                return value
        if isinstance(response.get("item"), list):
            return response["item"]
        return []

    def _summarize_homebox_item(self, item: dict, detail: bool = False):
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        parent = item.get("parent") if isinstance(item.get("parent"), dict) else {}
        tags = item.get("tags") or item.get("labels") or []
        tag_names = []
        for tag in tags if isinstance(tags, list) else []:
            if isinstance(tag, dict):
                name = tag.get("name")
            else:
                name = tag
            if name:
                tag_names.append(str(name))
        fields = item.get("fields") if isinstance(item.get("fields"), list) else []
        field_rows = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            value = field.get("textValue") or field.get("numberValue") or field.get("value") or ""
            field_rows.append({"name": str(field.get("name") or ""), "value": str(value)})
        summary = {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or "未命名物品"),
            "code": display_code(item),
            "rfid_code": rfid_epc_code(item),
            "url": item_url(config_snapshot().get("homebox_url", DEFAULT_HOMEBOX_URL), item),
            "location": str(
                item.get("locationName")
                or item.get("parentName")
                or location.get("name")
                or parent.get("name")
                or ""
            ),
            "tags": tag_names,
            "manufacturer": str(item.get("manufacturer") or ""),
            "model": str(item.get("modelNumber") or item.get("model") or ""),
            "quantity": item.get("quantity"),
            "description": str(item.get("description") or ""),
        }
        if detail:
            summary["notes"] = str(item.get("notes") or "")
            summary["fields"] = field_rows
            summary["raw_keys"] = sorted(str(key) for key in item.keys())
        return summary

    @staticmethod
    def _item_matches_query(item: dict, query: str) -> bool:
        needle = query.casefold()
        if not needle:
            return True
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        parent = item.get("parent") if isinstance(item.get("parent"), dict) else {}
        tags = item.get("tags") or item.get("labels") or []
        tag_text = " ".join(
            str(tag.get("name") if isinstance(tag, dict) else tag)
            for tag in tags if tag
        )
        text = " ".join(
            str(value)
            for value in (
                item.get("id"),
                item.get("assetId"),
                item.get("name"),
                item.get("description"),
                item.get("notes"),
                item.get("manufacturer"),
                item.get("modelNumber"),
                item.get("locationName"),
                item.get("parentName"),
                location.get("name"),
                parent.get("name"),
                tag_text,
                rfid_epc_code(item),
                display_code(item),
            )
            if value
        ).casefold()
        return needle in text

    @staticmethod
    def _find_score(item: dict, query: str) -> int:
        needle = query.casefold()
        name = item.get("name", "").casefold()
        code = item.get("code", "").casefold()
        rfid_code = item.get("rfid_code", "").casefold()
        hay = " ".join(str(item.get(key) or "") for key in ("name", "description", "location", "manufacturer", "model", "code", "rfid_code")).casefold()
        score = 0
        if needle == code or needle == rfid_code:
            score += 100
        if needle and needle in name:
            score += 60
        if needle and needle in hay:
            score += 20
        return score

    @staticmethod
    def _homebox_name_options(rows):
        names = []
        seen = set()
        for row in rows or []:
            name = str(row.get("name") or "").strip()
            if not name or name in seen:
                continue
            names.append(
                {
                    "id": str(row.get("id") or ""),
                    "name": name,
                    "description": str(row.get("description") or "").strip(),
                }
            )
            seen.add(name)
        return sorted(names, key=lambda item: item["name"].casefold())

    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = Path(unquote(path).lstrip("/"))
        target = (WEB_ROOT / rel).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.exists() or not target.is_file():
            return self.send_error(404, "Not found")
        self._send_file(target)

    def _serve_log_file(self, path):
        name = Path(unquote(path)).name
        target = (LOG_DIR / name).resolve()
        if not str(target).startswith(str(LOG_DIR.resolve())) or not target.exists() or not target.is_file():
            return self.send_error(404, "Not found")
        self._send_file(target)

    def _send_file(self, target):
        content = target.read_bytes()
        ctype = "application/octet-stream"
        if target.suffix.lower() == ".html":
            ctype = "text/html; charset=utf-8"
        elif target.suffix.lower() == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix.lower() == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif target.suffix.lower() in (".jpg", ".jpeg"):
            ctype = "image/jpeg"
        elif target.suffix.lower() == ".png":
            ctype = "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _selected_rfid_port(self, rfid: dict | None = None, config: dict | None = None):
        rfid = rfid or status_probe.snapshot().get("rfid") or {}
        configured = str((config or config_snapshot()).get("rfid_port") or "").strip()
        if configured and configured.lower() not in {"auto", "--"}:
            return configured
        configured = configured.upper()
        ports = rfid.get("ports") or []
        selected = next((item for item in ports if item.get("device", "").upper() == configured), None)
        return (selected or (ports or [{}])[0]).get("device")

    def _rfid_inventory(self, config: dict):
        rfid = status_probe.snapshot().get("rfid") or {}
        if not rfid.get("available") and not str(config.get("rfid_port") or "").strip():
            return {"ok": False, "message": rfid.get("message") or "未检测到可用 RFID 串口", "tags": []}
        port = self._selected_rfid_port(rfid, config)
        if not port:
            return {"ok": False, "message": "没有可打开的 RFID 串口", "tags": []}
        try:
            baud = int(os.getenv("RFID_BAUD", "115200"))
            with E710Reader(port=port, baudrate=baud) as reader:
                inventory = reader.inventory_once(repeat=3, wait_s=1.8)
            tags = sorted(
                inventory.get("tags", []),
                key=lambda tag: -999 if tag.get("rssi_dbm") is None else int(tag.get("rssi_dbm")),
                reverse=True,
            )
            return {
                "ok": True,
                "message": f"{port} 读到 {len(tags)} 个 RFID 标签",
                "port": port,
                "tags": tags,
                "default_epc": tags[0].get("epc") if tags else None,
                "target_payload": task_runner.rfid_payload_for_pending(),
                "inventory": inventory,
            }
        except Exception as exc:
            return {"ok": False, "message": f"{port} RFID 盘点失败: {exc}", "tags": []}

    def _rfid_probe(self, config: dict | None = None):
        rfid = status_probe.snapshot().get("rfid") or {}
        if not rfid.get("available") and not str((config or {}).get("rfid_port") or "").strip():
            return {"ok": False, "message": f"不能控制 RFID：{rfid.get('message', '未检测到可用设备')}", "rfid": rfid}
        port = self._selected_rfid_port(rfid, config)
        if not port:
            return {"ok": False, "message": "不能控制 RFID：没有可打开的串口", "rfid": rfid}
        try:
            baud = int(os.getenv("RFID_BAUD", "115200"))
            with E710Reader(port=port, baudrate=baud) as reader:
                result = reader.probe(wait_s=1.5)
            tags = result.get("inventory", {}).get("tags", [])
            epcs = ", ".join(
                f"{tag.get('epc_ascii')} ({tag.get('epc')})" if tag.get("epc_ascii") else tag.get("epc", "")
                for tag in tags
                if tag.get("epc")
            )
            info = result.get("info", {})
            message = f"{port} E710 OK"
            if info.get("firmware"):
                message += f" · FW {info['firmware']}"
            if info.get("output_power_dbm") is not None:
                message += f" · {info['output_power_dbm']} dBm"
            message += f" · 读到 {len(tags)} 个标签"
            if epcs:
                message += f": {epcs}"
            return {"ok": True, "message": message, "rfid": rfid, "probe": result}
        except E710Error as exc:
            return {"ok": False, "message": f"{port} E710 探测失败：{exc}", "rfid": rfid}
        except Exception as exc:
            return {"ok": False, "message": f"{port} RFID 探测异常：{exc}", "rfid": rfid}

    def _rfid_release(self, config: dict):
        rfid = status_probe.snapshot().get("rfid") or {}
        port = self._selected_rfid_port(rfid, config)
        if not port:
            return {"ok": False, "message": "未选择 RFID 串口"}

        baud = int(os.getenv("RFID_BAUD", "115200"))
        before_ok, before_error = self._test_rfid_port_open(port, baud)
        if before_ok:
            return {
                "ok": True,
                "message": f"{port} 当前可以打开，无需释放",
                "port": port,
                "killed": [],
            }

        candidates = self._orbit_process_candidates(port)
        killed = []
        for proc in candidates:
            pid = int(proc.get("pid") or 0)
            if not pid:
                continue
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                killed.append(proc)
            except Exception as exc:
                proc["kill_error"] = str(exc)

        time.sleep(0.6)
        after_ok, after_error = self._test_rfid_port_open(port, baud)
        if after_ok:
            message = f"{port} 已释放，可打开；结束了 {len(killed)} 个 O.R.B.I.T. 子进程"
        elif killed:
            message = f"{port} 仍无法打开；已结束 {len(killed)} 个 O.R.B.I.T. 子进程，可能被外部串口工具占用"
        else:
            message = f"{port} 无法打开，且没有发现可安全结束的 O.R.B.I.T. 子进程"
        return {
            "ok": after_ok,
            "message": message,
            "port": port,
            "killed": killed,
            "before_error": before_error,
            "after_error": after_error,
        }

    @staticmethod
    def _test_rfid_port_open(port: str, baud: int):
        try:
            with E710Reader(port=port, baudrate=baud):
                pass
            return True, None
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _orbit_process_candidates(port: str):
        if os.name != "nt":
            return []
        project_root = str(PROJECT_ROOT).lower()
        port_text = str(port or "").lower()
        script = f"""
$root = {json.dumps(project_root)}
$port = {json.dumps(port_text)}
$selfPid = {os.getpid()}
Get-CimInstance Win32_Process | Where-Object {{
  $_.ProcessId -ne $selfPid -and $_.CommandLine -and
  (
    $_.CommandLine.ToLower().Contains($root) -or
    $_.CommandLine.ToLower().Contains('\\vision\\')
  ) -and
  (
    $_.CommandLine.ToLower().Contains($port) -or
    $_.CommandLine.ToLower().Contains('rfid') -or
    $_.CommandLine.ToLower().Contains('e710') -or
    $_.CommandLine.ToLower().Contains('main.py') -or
    $_.CommandLine.ToLower().Contains('web_gui.py')
  )
}} | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress
"""
        try:
            raw = run_powershell_text(script, timeout=8)
            if not raw:
                return []
            data = json.loads(raw)
            rows = data if isinstance(data, list) else [data]
            return [
                {
                    "pid": int(row.get("ProcessId") or 0),
                    "name": str(row.get("Name") or ""),
                    "command": str(row.get("CommandLine") or "")[:220],
                }
                for row in rows
                if row.get("ProcessId")
            ]
        except Exception:
            return []

    def _camera_probe(self, config: dict):
        if config_bool(config.get("aux_camera_enabled"), "1") != "1":
            return {"ok": False, "message": "辅助视角已禁用"}

        errors = []
        for index in self._aux_index_candidates(config.get("aux_camera_index")):
            for backend_name, backend in self._aux_backend_candidates(config.get("aux_camera_backend")):
                cap = None
                try:
                    cap = cv2.VideoCapture(index, backend)
                    if not cap.isOpened():
                        errors.append(f"{index}/{backend_name}")
                        continue
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(config.get("aux_camera_width") or 1280))
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(config.get("aux_camera_height") or 720))
                    if config_bool(config.get("aux_camera_auto_exposure"), "1") == "1":
                        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
                    frame = None
                    deadline = time.time() + max(0.4, min(3.0, float(config.get("aux_camera_warmup_seconds") or 1.2)))
                    while time.time() < deadline:
                        ok, candidate = cap.read()
                        if ok and candidate is not None:
                            frame = candidate
                    if frame is None:
                        errors.append(f"{index}/{backend_name}: no frame")
                        continue
                    LOG_DIR.mkdir(exist_ok=True)
                    path = LOG_DIR / f"aux_probe_{int(time.time())}.jpg"
                    cv2.imwrite(str(path), frame)
                    return {
                        "ok": True,
                        "message": f"辅助相机测试成功: index={index}, backend={backend_name}",
                        "image": path.name,
                    }
                except Exception as exc:
                    errors.append(f"{index}/{backend_name}: {exc}")
                finally:
                    if cap is not None:
                        cap.release()
        return {"ok": False, "message": f"辅助相机测试失败，已尝试 {', '.join(errors[:12])}"}

    @staticmethod
    def _aux_index_candidates(value):
        text = str(value or "0").strip().lower()
        if text in {"", "auto"}:
            return [0, 1, 2, 3, 4, 5]
        try:
            return [int(text)]
        except ValueError:
            return [0, 1, 2, 3, 4, 5]

    @staticmethod
    def _aux_backend_candidates(value):
        backends = {
            "dshow": cv2.CAP_DSHOW,
            "msmf": cv2.CAP_MSMF,
            "any": cv2.CAP_ANY,
        }
        text = str(value or "auto").strip().lower()
        if text in backends:
            return [(text, backends[text])]
        return [("dshow", cv2.CAP_DSHOW), ("any", cv2.CAP_ANY), ("msmf", cv2.CAP_MSMF)]

    @staticmethod
    def _center_crop(image, ratio: float):
        ratio = min(1.0, max(0.35, float(ratio)))
        h, w = image.shape[:2]
        crop_w = int(w * ratio)
        crop_h = int(h * ratio)
        x1 = max(0, (w - crop_w) // 2)
        y1 = max(0, (h - crop_h) // 2)
        return image[y1 : y1 + crop_h, x1 : x1 + crop_w]

    def _ollama_models(self, parsed):
        query = parse_qs(parsed.query or "")
        base = normalize_ollama_base_url((query.get("url") or [DEFAULT_OLLAMA_URL])[0])
        result = {"ok": False, "base_url": base, "models": [], "ps": "", "message": ""}
        try:
            response = requests.get(f"{base}/api/tags", timeout=5)
            response.raise_for_status()
            models = response.json().get("models") or []
            result["models"] = [m.get("name") for m in models if m.get("name")]
            result["ok"] = True
        except Exception as exc:
            result["message"] = f"读取模型列表失败: {exc}"
            return result

        try:
            ps = requests.get(f"{base}/api/ps", timeout=3)
            if ps.ok:
                data = ps.json()
                rows = data.get("models") or []
                if rows:
                    result["ps"] = "\n".join(
                        f"{row.get('name','')} {row.get('size_vram') or row.get('size') or ''} {row.get('processor','')}"
                        for row in rows
                    )
                else:
                    result["ps"] = "当前未加载模型"
        except Exception:
            pass
        return result

    def _ai_models(self, payload):
        provider = str(payload.get("provider") or "ollama").strip().lower()
        if provider == "ollama":
            base = normalize_ollama_base_url(payload.get("base_url") or payload.get("url") or DEFAULT_OLLAMA_URL)
            result = {"ok": False, "provider": "ollama", "base_url": base, "models": [], "ps": "", "message": ""}
            try:
                response = requests.get(f"{base}/api/tags", timeout=5)
                response.raise_for_status()
                models = response.json().get("models") or []
                result["models"] = [m.get("name") for m in models if m.get("name")]
                result["ok"] = True
            except Exception as exc:
                result["message"] = f"读取模型列表失败: {exc}"
                return result

            try:
                ps = requests.get(f"{base}/api/ps", timeout=3)
                if ps.ok:
                    rows = ps.json().get("models") or []
                    result["ps"] = "\n".join(
                        f"{row.get('name','')} {row.get('size_vram') or row.get('size') or ''} {row.get('processor','')}"
                        for row in rows
                    ) or "当前未加载模型"
            except Exception:
                pass
            return result

        if provider not in {"openai", "gemini"}:
            provider = "openai"
        config = config_snapshot()
        api_key = str(payload.get("api_key") or config.get("ai_api_key") or "").strip()
        base = normalize_openai_base_url(payload.get("base_url") or config.get("ai_api_base"), provider)
        return list_openai_compatible_models(provider, base, api_key, timeout=8)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="O.R.B.I.T. Web GUI")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    scale_monitor.start()
    status_probe.start()
    server = OrbitHTTPServer((args.host, args.port), OrbitHandler)
    print(f"O.R.B.I.T. Web GUI listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scale_monitor.stop()
        status_probe.stop()
        server.server_close()


if __name__ == "__main__":
    main()
