import argparse
import hashlib
import json
import os
import re
import textwrap
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import win32print
except Exception:
    win32print = None


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
DEFAULT_PRINTER = os.getenv("ORBIT_LABEL_PRINTER", "TSC TTP-244 Pro")
DOTS_PER_MM = int(os.getenv("ORBIT_LABEL_DOTS_PER_MM", "8"))
LABEL_W_MM = 40
LABEL_H_MM = 20
LABEL_W = LABEL_W_MM * DOTS_PER_MM
LABEL_H = LABEL_H_MM * DOTS_PER_MM


def font(size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def item_code(item: dict) -> str:
    raw = item.get("assetId") or item.get("asset_id") or item.get("code") or item.get("id") or "ORBIT"
    text = str(raw).strip()
    if not text:
        text = "ORBIT"
    if not text.upper().startswith("ORB"):
        text = f"ORB-{text}"
    return text[:32]


def environment_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    if os.name != "nt":
        return ""
    try:
        import winreg
    except Exception:
        return ""

    locations = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    for root, key_path in locations:
        try:
            with winreg.OpenKey(root, key_path) as key:
                raw, _kind = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        value = str(raw or "").strip()
        if value:
            return value
    return ""


def owner_line(placeholder: str = "") -> str:
    line = " ".join(
        part
        for part in (
            environment_value("ORBIT_OWNER_NAME"),
            environment_value("ORBIT_OWNER_PHONE"),
        )
        if part
    )
    if line:
        return line
    return placeholder


def owner_home_url() -> str:
    return environment_value("ORBIT_OWNER_URL") or "https://sedirk.cn"


def display_code(item: dict) -> str:
    return rfid_epc_code(item)


def rfid_epc_code(item: dict) -> str:
    raw = str(item.get("assetId") or item.get("asset_id") or item.get("code") or item.get("id") or "").strip()
    readable = item_code(item).upper()
    if re.fullmatch(r"[A-Z0-9-]+", readable):
        if 8 <= len(readable) <= 12 and len(readable) % 2 == 0:
            return readable
        if readable.startswith("ORB-"):
            compact = "ORB-" + re.sub(r"[^A-Z0-9]", "", readable[4:])
            if 8 <= len(compact) <= 12 and len(compact) % 2 == 0:
                return compact
    clean = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    if len(clean) >= 8:
        suffix = clean[:8]
    else:
        source = raw or str(item.get("name") or "ORBIT")
        suffix = hashlib.blake2s(source.encode("utf-8"), digest_size=4).hexdigest().upper()
    return f"ORB-{suffix}"[:12]


def rfid_epc_hex(item: dict) -> str:
    return rfid_epc_code(item).encode("ascii").hex().upper()


def item_url(homebox_url: str, item: dict) -> str:
    for key in ("url", "pageUrl", "page_url", "publicUrl", "public_url"):
        value = str(item.get(key) or "").strip()
        if value:
            if value.startswith("/"):
                return f"{homebox_url.rstrip('/')}{value}"
            return value
    item_id = item.get("id") or ""
    return f"{homebox_url.rstrip('/')}/item/{item_id}" if item_id else homebox_url.rstrip("/")


def marker_id(code: str) -> int:
    digest = hashlib.blake2s(code.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % 1000


def render_text_lines(draw, xy, text, max_width, max_lines, font_obj, fill=0):
    x, y = xy
    lines = wrap_text(draw, text, max_width, font_obj, max_lines)
    for line in lines:
        draw.text((x, y), line, font=font_obj, fill=fill)
        y += text_height(draw, line, font_obj) + 2
    return y


def wrap_text(draw, text, max_width, font_obj, max_lines):
    text = str(text or "").strip()
    if not text:
        return []
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*|[\u4e00-\u9fff]+|[^\sA-Za-z0-9\u4e00-\u9fff]", text)
    lines = []
    current = ""
    for token in tokens:
        if not token.strip():
            continue
        joiner = "" if not current or re.match(r"^[^\w\u4e00-\u9fff]+$", token) else " "
        trial = current + joiner + token
        if text_width(draw, trial, font_obj) <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
        if len(lines) >= max_lines:
            break
        if text_width(draw, token, font_obj) <= max_width:
            current = token
        else:
            current = ellipsize(draw, token, max_width, font_obj)
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and text_width(draw, lines[-1], font_obj) > max_width:
        lines[-1] = ellipsize(draw, lines[-1], max_width, font_obj)
    return lines


def ellipsize(draw, text, max_width, font_obj):
    text = str(text or "")
    if text_width(draw, text, font_obj) <= max_width:
        return text
    suffix = "..."
    while text and text_width(draw, text + suffix, font_obj) > max_width:
        text = text[:-1]
    return text + suffix if text else suffix


def text_width(draw, text, font_obj):
    box = draw.textbbox((0, 0), text, font=font_obj)
    return box[2] - box[0]


def text_height(draw, text, font_obj):
    box = draw.textbbox((0, 0), text or "A", font=font_obj)
    return box[3] - box[1]


def aruco_image(code: str, size: int = 88) -> Image.Image:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_1000)
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id(code), size)
    return Image.fromarray(marker).convert("L")


def qr_image(payload: str, size: int = 88) -> Image.Image:
    params = cv2.QRCodeEncoder_Params()
    encoder = cv2.QRCodeEncoder.create(params)
    matrix = encoder.encode(payload)
    img = Image.fromarray(matrix).convert("L")
    scale = max(1, size // max(img.size))
    scaled = img.resize((img.size[0] * scale, img.size[1] * scale), Image.Resampling.NEAREST)
    canvas = Image.new("L", (size, size), 255)
    x = (size - scaled.size[0]) // 2
    y = (size - scaled.size[1]) // 2
    canvas.paste(scaled, (x, y))
    return canvas


def item_location_name(item: dict, ai: dict) -> str:
    if ai.get("suggested_location"):
        return str(ai["suggested_location"])
    if item.get("locationName"):
        return str(item["locationName"])
    location = item.get("location")
    if isinstance(location, dict):
        return str(location.get("name") or "")
    return ""


def human_label_image(item: dict, ai: Optional[dict] = None, measurement: Optional[dict] = None) -> Image.Image:
    ai = ai or {}
    measurement = measurement or {}
    name = item.get("name") or ai.get("name") or "未命名物品"
    visible_code = display_code(item)
    manufacturer = ai.get("manufacturer") or item.get("manufacturer") or ""
    model = item.get("modelNumber") or ai.get("model") or ""
    category = ai.get("category") or ""
    tags = " / ".join(str(tag) for tag in (ai.get("tags") or [])[:2])
    location = item_location_name(item, ai)
    weight = measurement.get("weight_g")
    width = measurement.get("width_mm")
    height = measurement.get("height_mm")

    img = Image.new("L", (LABEL_W, LABEL_H), 255)
    draw = ImageDraw.Draw(img)
    title_font = font(28, bold=True)
    body_font = font(18)
    small_font = font(15)
    code_font = font(18, bold=True)

    draw.rectangle((0, 0, LABEL_W - 1, LABEL_H - 1), outline=0, width=2)
    y = 5
    y = render_text_lines(draw, (10, y), name, LABEL_W - 20, 2, title_font)
    details = "  ".join(part for part in [manufacturer, model or category] if part)
    if details:
        y = render_text_lines(draw, (10, y + 1), details, LABEL_W - 20, 1, body_font)
    left_meta = []
    footer = visible_code
    if weight is not None:
        try:
            left_meta.append(f"{float(weight):.1f}g")
        except Exception:
            pass
    if width and height:
        try:
            left_meta.append(f"{float(width):.0f}x{float(height):.0f}mm")
        except Exception:
            pass
    if left_meta:
        y = render_text_lines(draw, (10, y + 1), "  ".join(left_meta), LABEL_W - 20, 1, small_font)
    if tags:
        y = render_text_lines(draw, (10, y + 1), tags, LABEL_W - 20, 1, small_font)
    if location:
        y = render_text_lines(draw, (10, y + 1), location, LABEL_W - 20, 1, small_font)
    draw.line((8, LABEL_H - 30, LABEL_W - 8, LABEL_H - 30), fill=0, width=1)
    draw.text((10, LABEL_H - 24), footer, font=code_font, fill=0)
    return img


def code_label_image(
    item: dict,
    homebox_url: str,
    ai: Optional[dict] = None,
    owner_placeholder: str = "",
) -> Image.Image:
    ai = ai or {}
    code = rfid_epc_code(item)
    visible_code = display_code(item)
    url = item_url(homebox_url, item)

    img = Image.new("L", (LABEL_W, LABEL_H), 255)
    draw = ImageDraw.Draw(img)
    small = font(13)
    tiny = font(11)
    code_font = font(18, bold=True)
    draw.rectangle((0, 0, LABEL_W - 1, LABEL_H - 1), outline=0, width=2)
    footer_y = LABEL_H - 30
    item_qr_size = 112
    item_qr_x = 132
    item_qr_y = 7
    home_qr_size = 54
    home_qr_x = LABEL_W - home_qr_size - 6
    home_qr_y = 18
    img.paste(qr_image(url, item_qr_size), (item_qr_x, item_qr_y))
    img.paste(qr_image(owner_home_url(), home_qr_size), (home_qr_x, home_qr_y))
    left_w = item_qr_x - 16
    owner_font = font(13, bold=True)
    owner = owner_line(owner_placeholder)
    if owner:
        render_text_lines(draw, (10, 6), owner, left_w, 1, owner_font)
        name_y = 28
    else:
        name_y = 8
    render_text_lines(draw, (10, name_y), item.get("name") or ai.get("name") or "", left_w, 2, small)
    marker_size = 48
    marker_y = 72
    img.paste(aruco_image(code, marker_size), (10, marker_y))
    draw.text((66, marker_y + 11), "AR ID", font=tiny, fill=0)
    draw.text((66, marker_y + 28), "物品页 QR", font=tiny, fill=0)
    draw.text((home_qr_x, home_qr_y + home_qr_size + 3), "sedirk.cn", font=tiny, fill=0)
    draw.line((8, footer_y, LABEL_W - 8, footer_y), fill=0, width=1)
    draw.text((10, LABEL_H - 24), visible_code, font=code_font, fill=0)
    return img


def image_to_tspl_bitmap(image: Image.Image) -> bytes:
    img = image.convert("L").resize((LABEL_W, LABEL_H))
    arr = (np.asarray(img) < 128).astype(np.uint8)
    packed = np.packbits(arr, axis=1, bitorder="big")
    width_bytes = packed.shape[1]
    header = (
        f"SIZE {LABEL_W_MM} mm,{LABEL_H_MM} mm\r\n"
        "GAP 2 mm,0 mm\r\n"
        "DENSITY 10\r\n"
        "SPEED 4\r\n"
        "DIRECTION 1\r\n"
        "REFERENCE 0,0\r\n"
        "CLS\r\n"
        f"BITMAP 0,0,{width_bytes},{LABEL_H},0,"
    ).encode("ascii")
    footer = b"\r\nPRINT 1,1\r\n"
    return header + packed.tobytes() + footer


def raw_print(printer_name: str, payload: bytes, document_name: str = "O.R.B.I.T. label"):
    if win32print is None:
        raise RuntimeError("pywin32/win32print is unavailable")
    ensure_printer_ready(printer_name)
    handle = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(handle, 1, (document_name, None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, payload)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def ensure_printer_ready(printer_name: str):
    handle = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(handle, 2)
    finally:
        win32print.ClosePrinter(handle)
    status = int(info.get("Status") or 0)
    blocked = {
        0x00000002: "error",
        0x00000010: "paper out",
        0x00000020: "manual feed",
        0x00000040: "paper problem",
        0x00000080: "offline",
        0x00000400: "printing error",
        0x00000800: "output bin full",
        0x00001000: "not available",
        0x00020000: "toner low",
        0x00040000: "no toner",
        0x00400000: "door open",
    }
    reasons = [name for bit, name in blocked.items() if status & bit]
    if reasons:
        raise RuntimeError(
            f"printer {printer_name!r} is not ready: status={status} ({', '.join(reasons)})"
        )


def save_preview(image: Image.Image, name: str) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / name
    image.save(path)
    return path


def print_label_image(image: Image.Image, printer_name: str = DEFAULT_PRINTER, document_name: str = "O.R.B.I.T. label"):
    raw_print(printer_name, image_to_tspl_bitmap(image), document_name=document_name)


def print_item_label_set(
    item: dict,
    ai: Optional[dict] = None,
    measurement: Optional[dict] = None,
    homebox_url: str = "http://192.168.31.3:3100",
    printer_name: str = DEFAULT_PRINTER,
    dry_run: bool = False,
) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    code = rfid_epc_code(item)
    human = human_label_image(item, ai=ai, measurement=measurement)
    coded = code_label_image(
        item,
        homebox_url=homebox_url,
        ai=ai,
        owner_placeholder="[Owner Tel]" if dry_run else "",
    )
    human_preview = save_preview(human, f"label_human_{code}_{stamp}.png")
    code_preview = save_preview(coded, f"label_code_{code}_{stamp}.png")
    if not dry_run:
        print_label_image(human, printer_name, "O.R.B.I.T. human label")
        print_label_image(coded, printer_name, "O.R.B.I.T. AR QR label")
    return {
        "code": code,
        "human_preview": str(human_preview),
        "code_preview": str(code_preview),
        "printed": not dry_run,
        "rfid_payload": rfid_payload(item, homebox_url),
    }


def rfid_payload(item: dict, homebox_url: str) -> dict:
    epc_code = rfid_epc_code(item)
    return {
        "code": epc_code,
        "epc_code": epc_code,
        "epc_hex_candidate": rfid_epc_hex(item),
        "url": item_url(homebox_url, item),
    }


def build_parser():
    parser = argparse.ArgumentParser(description="Print O.R.B.I.T. 40x20mm TSC labels.")
    parser.add_argument("--printer", default=DEFAULT_PRINTER)
    parser.add_argument("--homebox-url", default=os.getenv("HOMEBOX_URL", "http://192.168.31.3:3100"))
    parser.add_argument("--item-json", help="JSON file containing item/ai/measurement data")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test", action="store_true", help="Print a single harmless printer test label")
    return parser


def main():
    args = build_parser().parse_args()
    if args.item_json:
        data = json.loads(Path(args.item_json).read_text(encoding="utf-8"))
        item = data.get("item") or data.get("ai") or data
        ai = data.get("ai") or {}
        measurement = data.get("measurement") or {}
    else:
        item = {"id": "PRINT-TEST", "assetId": "PRINT-TEST", "name": "O.R.B.I.T. 打印测试"}
        ai = {"name": item["name"], "category": "系统测试", "model": "40x20"}
        measurement = {"weight_g": 0}
    if args.test:
        image = human_label_image(item, ai=ai, measurement=measurement)
        preview = save_preview(image, f"label_print_test_{time.strftime('%Y%m%d-%H%M%S')}.png")
        if not args.dry_run:
            print_label_image(image, args.printer, "O.R.B.I.T. print test")
        print(json.dumps({"preview": str(preview), "printed": not args.dry_run}, ensure_ascii=False, indent=2))
        return 0
    result = print_item_label_set(item, ai=ai, measurement=measurement, homebox_url=args.homebox_url, printer_name=args.printer, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
