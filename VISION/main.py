import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import requests

from homebox import HomeboxClient, HomeboxError
from rfid_e710 import E710Error, E710Reader
from scale import ElectronicScaleReader
from vision import IntelligentScanner

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
DEFAULT_HOMEBOX_URL = os.getenv("HOMEBOX_URL", "http://192.168.31.3:3100")
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/chat")
DEFAULT_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
DEFAULT_OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
DEFAULT_OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "192"))
DEFAULT_AI_PROVIDER = os.getenv("ORBIT_AI_PROVIDER", "ollama")
DEFAULT_AI_API_BASE = os.getenv("ORBIT_AI_API_BASE", "")
DEFAULT_AI_API_KEY = os.getenv("ORBIT_AI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
SCAN_MODES = ["scale", "auto", "full", "macro", "furniture"]
DEFAULT_SCAN_MODE = os.getenv("ORBIT_SCAN_MODE", os.getenv("SCAN_MODE", "auto"))
if DEFAULT_SCAN_MODE not in SCAN_MODES:
    DEFAULT_SCAN_MODE = "auto"


def build_parser():
    parser = argparse.ArgumentParser(description="O.R.B.I.T. 入库主程序")
    subparsers = parser.add_subparsers(dest="command")

    def add_common(p):
        p.add_argument("--homebox-url", default=DEFAULT_HOMEBOX_URL)
        p.add_argument("--homebox-token", default=os.getenv("HOMEBOX_TOKEN"))
        p.add_argument("--homebox-username", default=os.getenv("HOMEBOX_USERNAME"))
        p.add_argument("--homebox-password", default=os.getenv("HOMEBOX_PASSWORD"))
        p.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
        p.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
        p.add_argument("--ai-provider", default=DEFAULT_AI_PROVIDER)
        p.add_argument("--ai-api-base", default=DEFAULT_AI_API_BASE)
        p.add_argument("--ai-api-key", default=DEFAULT_AI_API_KEY)
        p.add_argument("--ollama-timeout", type=int, default=int(os.getenv("OLLAMA_TIMEOUT", "150")))
        p.add_argument("--ollama-num-gpu", type=int, default=int(os.getenv("OLLAMA_NUM_GPU")) if os.getenv("OLLAMA_NUM_GPU") else None)
        p.add_argument("--ollama-num-ctx", type=int, default=DEFAULT_OLLAMA_NUM_CTX)
        p.add_argument("--ollama-num-predict", type=int, default=DEFAULT_OLLAMA_NUM_PREDICT)
        p.add_argument("--ai-image-max-size", type=int, default=int(os.getenv("ORBIT_AI_IMAGE_MAX_SIZE", "0")))
        p.add_argument("--ai-image-jpeg-quality", type=int, default=int(os.getenv("ORBIT_AI_IMAGE_JPEG_QUALITY", "80")))
        p.add_argument("--mode", choices=SCAN_MODES, default=DEFAULT_SCAN_MODE)
        p.add_argument("--dry-run", action="store_true", help="只识别和打印，不写入 Homebox")

    run = subparsers.add_parser("run", help="电子秤稳定后自动扫描入库")
    add_common(run)
    run.add_argument("--scale-port", default=os.getenv("SCALE_PORT", "auto"))
    run.add_argument("--scale-baud", type=int, default=int(os.getenv("SCALE_BAUD", "9600")))
    run.add_argument("--min-weight-g", type=float, default=float(os.getenv("MIN_WEIGHT_G", "5")))
    run.add_argument("--stable-count", type=int, default=3)
    run.add_argument("--tolerance-g", type=float, default=0.3)
    run.add_argument("--max-scans", type=int, default=0, help="0 表示一直运行")
    run.add_argument("--manual", action="store_true", help="使用 OpenCV 取景器和空格键手动触发")
    run.set_defaults(func=cmd_run)

    scan = subparsers.add_parser("scan-once", help="立即扫描一次")
    add_common(scan)
    scan.add_argument("--weight-g", type=float)
    scan.set_defaults(func=cmd_scan_once)

    capture = subparsers.add_parser("capture", help="只拍摄并保存主相机图像，不调用 Ollama")
    add_common(capture)
    capture.set_defaults(func=cmd_capture)

    identify = subparsers.add_parser("identify-image", help="识别已有图片，可配合 GUI 框选区域使用")
    add_common(identify)
    identify.add_argument("--image", required=True)
    identify.add_argument("--aux-image")
    identify.add_argument("--measurement-json")
    identify.add_argument("--bbox", help="归一化框选区域 x,y,w,h")
    identify.add_argument("--weight-g", type=float)
    identify.set_defaults(func=cmd_identify_image)

    live_identify = subparsers.add_parser("identify-selection-live", help="按 GUI 框选区域重拍曝光后识别")
    add_common(live_identify)
    live_identify.add_argument("--image", required=True, help="原始参考图，用于在重拍失败时回退")
    live_identify.add_argument("--aux-image")
    live_identify.add_argument("--measurement-json")
    live_identify.add_argument("--bbox", required=True, help="归一化框选区域 x,y,w,h")
    live_identify.add_argument("--weight-g", type=float)
    live_identify.set_defaults(func=cmd_identify_selection_live)

    scale = subparsers.add_parser("scale", help="打印电子秤串口读数")
    scale.add_argument("--scale-port", default=os.getenv("SCALE_PORT", "auto"))
    scale.add_argument("--scale-baud", type=int, default=int(os.getenv("SCALE_BAUD", "9600")))
    scale.add_argument("--seconds", type=float, default=30)
    scale.set_defaults(func=cmd_scale)

    rfid = subparsers.add_parser("rfid-read", help="读取 E710/IE701 RFID 标签 EPC")
    rfid.add_argument("--rfid-port", default=os.getenv("RFID_PORT", "COM3"))
    rfid.add_argument("--rfid-baud", type=int, default=int(os.getenv("RFID_BAUD", "115200")))
    rfid.add_argument("--seconds", type=float, default=3.0)
    rfid.add_argument("--repeat", type=int, default=1)
    rfid.set_defaults(func=cmd_rfid_read)

    rfid_write = subparsers.add_parser("rfid-write", help="写入 E710/IE701 RFID 标签 EPC")
    rfid_write.add_argument("--rfid-port", default=os.getenv("RFID_PORT", "COM3"))
    rfid_write.add_argument("--rfid-baud", type=int, default=int(os.getenv("RFID_BAUD", "115200")))
    rfid_write.add_argument("--epc-hex", required=True, help="要写入的 EPC HEX，长度必须按 word 对齐")
    rfid_write.add_argument("--password-hex", default=os.getenv("RFID_ACCESS_PASSWORD", "00000000"))
    rfid_write.add_argument("--allow-multiple", action="store_true", help="允许天线前多张标签，默认禁止")
    rfid_write.add_argument("--target-epc", default=None, help="select an existing EPC before writing")
    rfid_write.set_defaults(func=cmd_rfid_write)

    diagnose = subparsers.add_parser("diagnose", help="检查 Python/硬件/Ollama/Homebox")
    add_common(diagnose)
    diagnose.add_argument("--scale-port", default=os.getenv("SCALE_PORT", "auto"))
    diagnose.set_defaults(func=cmd_diagnose)

    parser.set_defaults(func=cmd_run, command="run")
    return parser


def build_homebox(args) -> HomeboxClient:
    client = HomeboxClient(
        args.homebox_url,
        token=args.homebox_token,
        username=args.homebox_username,
        password=args.homebox_password,
    )
    try:
        health = client.health()
        build = health.get("build") or {}
        print(f"Homebox OK: {health.get('title')} {build.get('version')} @ {args.homebox_url}")
    except Exception as exc:
        print(f"Homebox health check failed: {exc}")

    if client.authenticated:
        print("Homebox auth: ready")
    elif not args.dry_run:
        print("Homebox auth: missing token/login; this run will switch to dry-run output")
        args.dry_run = True
    return client


def homebox_context(client: HomeboxClient) -> tuple[list[str], list[str]]:
    if not client.authenticated:
        return [], []
    try:
        source_tags = {"General", "AI识别", "O.R.B.I.T."}
        tags = [tag.get("name", "") for tag in client.get_tags()]
        tags = [tag for tag in tags if tag and tag not in source_tags]
    except Exception as exc:
        print(f"Homebox tags unavailable: {exc}")
        tags = []
    try:
        locations = [loc.get("name", "") for loc in client.get_locations()]
    except Exception as exc:
        print(f"Homebox locations unavailable: {exc}")
        locations = []
    return [t for t in tags if t], [l for l in locations if l]


def save_scan_image(image) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"orbit_scan_{int(time.time())}.jpg"
    cv2.imwrite(str(path), image)
    return path


def save_scan_result(image_path: Path, ai_data: dict, measurement: dict) -> Path:
    result_path = image_path.with_suffix(".json")
    payload = {
        "ai": ai_data,
        "measurement": measurement,
        "image": str(image_path),
        "saved_at": time.time(),
    }
    result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return result_path


def save_capture_result(image_path: Path, measurement: dict) -> Path:
    result_path = image_path.with_suffix(".json")
    payload = {
        "ai": None,
        "measurement": measurement,
        "image": str(image_path),
        "saved_at": time.time(),
    }
    result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return result_path


def store_result(client, args, ai_data, image, measurement):
    image_path = save_scan_image(image)
    result_path = save_scan_result(image_path, ai_data, measurement)
    print(f"scan image saved: {image_path}")
    print(f"scan result saved: {result_path}")
    print(json.dumps({"ai": ai_data, "measurement": measurement}, indent=2, ensure_ascii=False))

    if args.dry_run:
        print("dry-run: skipped Homebox write")
        return

    try:
        client.create_item(
            ai_data,
            image_cv2=image,
            weight_g=measurement.get("weight_g"),
            dimensions=measurement,
            dry_run=False,
        )
        print("入库完成")
    except HomeboxError as exc:
        print(f"入库失败: {exc}")


def make_scanner(args) -> IntelligentScanner:
    scanner = IntelligentScanner(
        ollama_model=args.ollama_model,
        ollama_api_url=args.ollama_url,
        ollama_timeout=args.ollama_timeout,
        ollama_num_gpu=args.ollama_num_gpu,
        ollama_num_ctx=args.ollama_num_ctx,
        ollama_num_predict=args.ollama_num_predict,
        ai_provider=args.ai_provider,
        ai_api_base=args.ai_api_base,
        ai_api_key=args.ai_api_key,
    )
    scanner.ai_image_max_size = args.ai_image_max_size
    scanner.ai_image_jpeg_quality = args.ai_image_jpeg_quality
    return scanner


def make_ai_only_scanner(args) -> IntelligentScanner:
    scanner = object.__new__(IntelligentScanner)
    scanner.ollama_model = args.ollama_model
    scanner.ollama_api_url = args.ollama_url
    scanner.ai_provider = (args.ai_provider or DEFAULT_AI_PROVIDER or "ollama").strip().lower()
    scanner.ai_api_base = (args.ai_api_base or DEFAULT_AI_API_BASE or "").strip()
    scanner.ai_api_key = (args.ai_api_key or DEFAULT_AI_API_KEY or "").strip()
    scanner.ollama_timeout = args.ollama_timeout
    scanner.ollama_num_gpu = args.ollama_num_gpu
    scanner.ollama_num_ctx = args.ollama_num_ctx
    scanner.ollama_num_predict = args.ollama_num_predict
    scanner.ollama_keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "15m")
    scanner.ai_composite_view = os.getenv("ORBIT_AI_COMPOSITE_VIEW", "0") != "0"
    scanner.ai_image_max_size = args.ai_image_max_size
    scanner.ai_image_jpeg_quality = args.ai_image_jpeg_quality
    scanner.ai_enhance = os.getenv("ORBIT_AI_ENHANCE", "1") != "0"
    scanner.exposure_target_mean = float(os.getenv("ORBIT_EXPOSURE_TARGET_MEAN", "54"))
    scanner.exposure_low_mean = float(os.getenv("ORBIT_EXPOSURE_LOW_MEAN", "42"))
    scanner.exposure_highlight_p98 = float(os.getenv("ORBIT_EXPOSURE_HIGHLIGHT_P98", "242"))
    scanner.exposure_clip_ratio = float(os.getenv("ORBIT_EXPOSURE_CLIP_RATIO", "0.012"))
    scanner.aux_camera_center_crop = float(os.getenv("ORBIT_AUX_CAMERA_CENTER_CROP", "1"))
    return scanner


def parse_normalized_bbox(value: str | None):
    if not value:
        return None
    parts = [float(p) for p in value.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 4:
        raise ValueError("bbox must be x,y,w,h")
    x, y, w, h = parts
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.01), 1.0 - x)
    h = min(max(h, 0.01), 1.0 - y)
    return x, y, w, h


def crop_by_bbox(image, bbox):
    if not bbox:
        return image, None
    img_h, img_w = image.shape[:2]
    x, y, w, h = bbox
    x1 = int(round(x * img_w))
    y1 = int(round(y * img_h))
    x2 = int(round((x + w) * img_w))
    y2 = int(round((y + h) * img_h))
    x1 = max(0, min(x1, img_w - 1))
    y1 = max(0, min(y1, img_h - 1))
    x2 = max(x1 + 1, min(x2, img_w))
    y2 = max(y1 + 1, min(y2, img_h))
    return image[y1:y2, x1:x2], {"x": x, "y": y, "w": w, "h": h, "image_w": img_w, "image_h": img_h}


def load_measurement(path_value: str | None):
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get("measurement") or {}


def selected_measurement(base: dict, bbox, weight_g=None):
    measurement = dict(base or {})
    if weight_g is not None:
        measurement["weight_g"] = weight_g
    if bbox:
        x, y, w, h = bbox
        measurement["manual_selection_bbox"] = {"x": x, "y": y, "w": w, "h": h}
        frame_w = measurement.get("selection_frame_width_mm")
        frame_h = measurement.get("selection_frame_height_mm")
        if frame_w and frame_h:
            measurement["width_mm"] = float(frame_w) * w
            measurement["height_mm"] = float(frame_h) * h
            measurement["measurement_source"] = "manual_bbox_full_view_scaled"
            measurement["measurement_reliable"] = False
        elif measurement.get("measurement_reliable") is not False:
            width = measurement.get("width_mm")
            height = measurement.get("height_mm")
            if width and height:
                measurement["width_mm"] = float(width) * w
                measurement["height_mm"] = float(height) * h
                measurement["measurement_source"] = "manual_bbox_scaled"
                measurement["measurement_reliable"] = True
            else:
                measurement["measurement_reliable"] = False
                measurement["measurement_source"] = "manual_bbox_no_depth"
        else:
            measurement["measurement_source"] = "manual_bbox_unreliable_base"
    return measurement


def cmd_capture(args):
    scanner = make_scanner(args)
    try:
        image, measurement = scanner.capture_object(mode=args.mode)
        if image is None:
            print("拍摄失败：未获得有效裁图")
            return
        image_path = save_scan_image(image)
        result_path = save_capture_result(image_path, measurement)
        print(f"capture image saved: {image_path}")
        print(f"capture result saved: {result_path}")
        print(json.dumps(measurement, indent=2, ensure_ascii=False))
    finally:
        scanner.release()


def cmd_identify_image(args):
    client = build_homebox(args)
    labels, locations = homebox_context(client)
    image_path = Path(args.image)
    image = cv2.imread(str(image_path))
    if image is None:
        print(f"识别失败：无法读取图片 {image_path}")
        return

    bbox = parse_normalized_bbox(args.bbox)
    crop, bbox_meta = crop_by_bbox(image, bbox)
    base_measurement = load_measurement(args.measurement_json)
    measurement = selected_measurement(base_measurement, bbox, weight_g=args.weight_g)
    measurement["source_image"] = str(image_path)
    if bbox_meta:
        measurement["selection_bbox"] = bbox_meta

    aux_image = None
    if args.aux_image:
        aux_image = cv2.imread(str(Path(args.aux_image)))

    width = measurement.get("width_mm")
    height = measurement.get("height_mm")
    scanner = make_ai_only_scanner(args)
    ai_response = scanner.ask_ollama(
        crop,
        width,
        height,
        weight_g=measurement.get("weight_g"),
        labels=labels,
        locations=locations,
        mode=args.mode,
        aux_image=aux_image,
        context_image=image if bbox else None,
        selection_bbox=bbox,
    )
    ai_data = scanner.normalize_ai_data(scanner.parse_ai_json(ai_response), labels=labels, locations=locations)
    if not ai_data:
        print("识别失败：未获得有效 AI JSON")
        return

    print(json.dumps(ai_data, indent=4, ensure_ascii=False))
    store_result(client, args, ai_data, crop, measurement)


def cmd_identify_selection_live(args):
    client = build_homebox(args)
    labels, locations = homebox_context(client)
    bbox = parse_normalized_bbox(args.bbox)
    if not bbox:
        print("识别失败：缺少框选区域")
        return

    base_measurement = load_measurement(args.measurement_json)
    scanner = make_scanner(args)
    reuse_aux_image = (
        bool(args.aux_image)
        and os.getenv("ORBIT_AUX_RECAPTURE_ON_SELECTION", "0") == "0"
    )
    if reuse_aux_image:
        scanner.aux_camera_enabled = False
    source_image = None
    live_measurement = {}
    try:
        source_image, live_measurement = scanner.capture_object(
            mode=args.mode,
            weight_g=args.weight_g,
            selection_bbox=bbox,
        )
        aux_image = scanner.last_aux_image
        if aux_image is None and args.aux_image:
            aux_image = cv2.imread(str(Path(args.aux_image)))
            if reuse_aux_image and aux_image is not None:
                print("⏱️ 已复用第一次拍照的辅助视角，跳过 C270 重采集")
        if source_image is not None:
            source_path = save_scan_image(source_image)
            print(f"capture image saved: {source_path}")
        else:
            source_path = Path(args.image)
            source_image = cv2.imread(str(source_path))
            aux_image = cv2.imread(str(Path(args.aux_image))) if args.aux_image else None
            print("⚠️ 框选重拍失败，回退到原始图片识别")
    finally:
        scanner.release()

    if source_image is None:
        print(f"识别失败：无法读取图片 {args.image}")
        return

    merged_measurement = {**base_measurement, **(live_measurement or {})}
    measurement = selected_measurement(merged_measurement, bbox, weight_g=args.weight_g)
    measurement["source_image"] = str(source_path)

    crop, bbox_meta = crop_by_bbox(source_image, bbox)
    if bbox_meta:
        measurement["selection_bbox"] = bbox_meta

    width = measurement.get("width_mm")
    height = measurement.get("height_mm")
    ai_response = scanner.ask_ollama(
        crop,
        width,
        height,
        weight_g=measurement.get("weight_g"),
        labels=labels,
        locations=locations,
        mode=args.mode,
        aux_image=aux_image,
        context_image=source_image,
        selection_bbox=bbox,
    )
    ai_data = scanner.normalize_ai_data(scanner.parse_ai_json(ai_response), labels=labels, locations=locations)
    if not ai_data:
        print("识别失败：未获得有效 AI JSON")
        return

    print(json.dumps(ai_data, indent=4, ensure_ascii=False))
    store_result(client, args, ai_data, crop, measurement)


def cmd_scan_once(args):
    client = build_homebox(args)
    labels, locations = homebox_context(client)
    scanner = make_scanner(args)
    try:
        ai_data, image, measurement = scanner.capture_and_identify(
            mode=args.mode,
            weight_g=args.weight_g,
            labels=labels,
            locations=locations,
        )
        if ai_data and image is not None:
            store_result(client, args, ai_data, image, measurement)
        else:
            print("扫描失败：未获得有效 AI JSON 或图像")
    finally:
        scanner.release()


def cmd_run(args):
    client = build_homebox(args)
    labels, locations = homebox_context(client)
    scanner = make_scanner(args)

    if args.manual:
        def on_scan(ai_data, image):
            measurement = {"weight_g": None}
            store_result(client, args, ai_data, image, measurement)

        try:
            scanner.run(on_success_callback=on_scan)
        finally:
            scanner.release()
        return

    scale = ElectronicScaleReader(port=args.scale_port, baudrate=args.scale_baud)
    print(f"Scale ready on {scale.port} @ {args.scale_baud}. Put an item on the scale.")
    scans = 0
    try:
        while args.max_scans <= 0 or scans < args.max_scans:
            reading = scale.wait_for_stable(
                min_weight_g=args.min_weight_g,
                stable_count=args.stable_count,
                tolerance_g=args.tolerance_g,
            )
            print(f"Stable trigger: {reading.weight_g:.1f} g")
            ai_data, image, measurement = scanner.capture_and_identify(
                mode=args.mode,
                weight_g=reading.weight_g,
                labels=labels,
                locations=locations,
            )
            if ai_data and image is not None:
                store_result(client, args, ai_data, image, measurement)
                scans += 1
            else:
                print("扫描失败：请调整物体位置后重试")
            wait_until_removed(scale, args.min_weight_g)
            print("Ready for next item")
    except KeyboardInterrupt:
        print("Stopped by user")
    finally:
        scale.close()
        scanner.release()


def wait_until_removed(scale: ElectronicScaleReader, min_weight_g: float) -> None:
    print("Remove the item to arm the next trigger.")
    while True:
        reading = scale.read_once()
        if not reading:
            continue
        if reading.weight_g < max(1.0, min_weight_g * 0.5):
            return


def cmd_scale(args):
    reader = ElectronicScaleReader(port=args.scale_port, baudrate=args.scale_baud)
    print(f"Reading {reader.port} @ {args.scale_baud} for {args.seconds}s")
    end = time.time() + args.seconds
    try:
        while time.time() < end:
            reading = reader.read_once()
            if reading:
                print(
                    f"{reading.weight_g:.1f} g "
                    f"{'stable' if reading.stable else 'moving'} raw={reading.raw}"
                )
    finally:
        reader.close()


def cmd_rfid_read(args):
    seen: dict[str, dict] = {}
    try:
        with E710Reader(port=args.rfid_port, baudrate=args.rfid_baud) as reader:
            info = reader.query_info()
            print(json.dumps({"rfid": info}, indent=2, ensure_ascii=False))
            deadline = time.time() + max(0.5, args.seconds)
            while time.time() < deadline:
                inventory = reader.inventory_once(repeat=args.repeat, wait_s=0.8)
                for tag in inventory.get("tags", []):
                    seen[tag["epc"]] = tag
                    print(json.dumps(tag, ensure_ascii=False))
                if inventory.get("errors"):
                    print(json.dumps({"errors": inventory["errors"]}, ensure_ascii=False))
    except E710Error as exc:
        print(f"RFID read failed: {exc}")
        return
    print(json.dumps({"tag_count": len(seen), "tags": list(seen.values())}, indent=2, ensure_ascii=False))


def cmd_rfid_write(args):
    try:
        with E710Reader(port=args.rfid_port, baudrate=args.rfid_baud) as reader:
            result = reader.write_epc_hex(
                args.epc_hex,
                password_hex=args.password_hex,
                require_single=not args.allow_multiple and not args.target_epc,
                expected_current_epc=args.target_epc,
                verify=True,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except E710Error as exc:
        print(f"RFID write failed: {exc}")


def cmd_diagnose(args):
    print(f"Python: {sys.executable}")
    print("Serial ports:")
    for port in ElectronicScaleReader.available_ports():
        print(f"  {port['device']} {port['description']} {port['hwid']}")

    rfid_port = os.getenv("RFID_PORT", "COM3")
    try:
        with E710Reader(port=rfid_port, baudrate=int(os.getenv("RFID_BAUD", "115200"))) as reader:
            print(f"RFID E710: {json.dumps(reader.probe(wait_s=0.8), ensure_ascii=False)}")
    except Exception as exc:
        print(f"RFID E710 check failed: {exc}")

    try:
        import pyrealsense2 as rs

        devices = list(rs.context().query_devices())
        print(f"RealSense devices: {len(devices)}")
        for device in devices:
            print(
                "  "
                + device.get_info(rs.camera_info.name)
                + " "
                + device.get_info(rs.camera_info.serial_number)
            )
    except Exception as exc:
        print(f"RealSense check failed: {exc}")

    try:
        response = requests.get(args.ollama_url.replace("/api/chat", "/api/tags"), timeout=10)
        print(f"Ollama tags: {response.status_code} {response.text[:500]}")
    except Exception as exc:
        print(f"Ollama check failed: {exc}")

    client = build_homebox(args)
    if client.authenticated:
        tags, locations = homebox_context(client)
        print(f"Homebox tags: {len(tags)}, locations: {len(locations)}")


def main():
    parser = build_parser()
    argv = sys.argv[1:] or ["run"]
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
