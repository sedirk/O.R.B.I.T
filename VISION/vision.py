import pyrealsense2 as rs
import numpy as np
import cv2
import time
import base64
import requests
import json
import os
import sys
import re
from pathlib import Path

_OPEN3D = None
_REMBG_REMOVE = None


def _open3d():
    global _OPEN3D
    if _OPEN3D is None:
        import open3d as open3d_module

        _OPEN3D = open3d_module
    return _OPEN3D


def _remove_background(image_rgb):
    global _REMBG_REMOVE
    if _REMBG_REMOVE is None:
        from rembg import remove as remove_background

        _REMBG_REMOVE = remove_background
    return _REMBG_REMOVE(image_rgb)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
# ================= 配置区域 =================
# 1. 确保这里写的名字和你 ollama list 里的一模一样！
#    如果是 qwen3-vl:latest，就写 qwen3-vl
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")

# 2. 强制使用 Chat 接口 (视觉模型的标准)
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/chat")
AI_PROVIDER = os.getenv("ORBIT_AI_PROVIDER", "ollama").strip().lower()
AI_API_BASE = os.getenv("ORBIT_AI_API_BASE", "").strip()
AI_API_KEY = os.getenv("ORBIT_AI_API_KEY", "").strip()
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "150"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
OLLAMA_NUM_GPU = os.getenv("OLLAMA_NUM_GPU", "36")
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "192"))
CLOUD_MAX_TOKENS = int(os.getenv("ORBIT_CLOUD_MAX_TOKENS", "0"))
AI_IMAGE_MAX_SIZE = int(os.getenv("ORBIT_AI_IMAGE_MAX_SIZE", "0"))
AI_IMAGE_JPEG_QUALITY = int(os.getenv("ORBIT_AI_IMAGE_JPEG_QUALITY", "80"))
AI_COMPOSITE_VIEW = os.getenv("ORBIT_AI_COMPOSITE_VIEW", "0") != "0"
SEGMENT_MAX_SIZE = int(os.getenv("ORBIT_SEGMENT_MAX_SIZE", "960"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "15m")
SCALE_ROI = os.getenv("ORBIT_SCALE_ROI", "0.49,0.22,0.34,0.48")
SCALE_DEPTH_DELTA_MM = float(os.getenv("ORBIT_SCALE_DEPTH_DELTA_MM", "35"))
SCALE_AI_ROTATE = int(os.getenv("ORBIT_SCALE_AI_ROTATE", "180"))
SCALE_OBJECT_MIN_AREA_RATIO = float(os.getenv("ORBIT_SCALE_OBJECT_MIN_AREA_RATIO", "0.001"))
SCALE_OBJECT_MAX_AREA_RATIO = float(os.getenv("ORBIT_SCALE_OBJECT_MAX_AREA_RATIO", "0.22"))
SCALE_OBJECT_MAX_WIDTH_RATIO = float(os.getenv("ORBIT_SCALE_OBJECT_MAX_WIDTH_RATIO", "0.58"))
SCALE_OBJECT_MAX_HEIGHT_RATIO = float(os.getenv("ORBIT_SCALE_OBJECT_MAX_HEIGHT_RATIO", "0.58"))
SCALE_OBJECT_PAD = int(os.getenv("ORBIT_SCALE_OBJECT_PAD", "30"))
EXPOSURE_RETRY = os.getenv("ORBIT_EXPOSURE_RETRY", "1") != "0"
EXPOSURE_TARGET_MEAN = float(os.getenv("ORBIT_EXPOSURE_TARGET_MEAN", "54"))
EXPOSURE_LOW_MEAN = float(os.getenv("ORBIT_EXPOSURE_LOW_MEAN", "42"))
EXPOSURE_HIGH_MEAN = float(os.getenv("ORBIT_EXPOSURE_HIGH_MEAN", "175"))
EXPOSURE_HIGHLIGHT_P98 = float(os.getenv("ORBIT_EXPOSURE_HIGHLIGHT_P98", "242"))
EXPOSURE_CLIP_RATIO = float(os.getenv("ORBIT_EXPOSURE_CLIP_RATIO", "0.012"))
EXPOSURE_SETTLE_FRAMES = int(os.getenv("ORBIT_EXPOSURE_SETTLE_FRAMES", "8"))
EXPOSURE_ROI = os.getenv("ORBIT_EXPOSURE_ROI", "0") != "0"
AI_ENHANCE = os.getenv("ORBIT_AI_ENHANCE", "1") != "0"
AUX_CAMERA_ENABLED = os.getenv("ORBIT_AUX_CAMERA_ENABLED", "1") != "0"
AUX_CAMERA_INDEX = os.getenv("ORBIT_AUX_CAMERA_INDEX", "0")
AUX_CAMERA_BACKEND = os.getenv("ORBIT_AUX_CAMERA_BACKEND", "dshow")
AUX_CAMERA_WIDTH = int(os.getenv("ORBIT_AUX_CAMERA_WIDTH", "1280"))
AUX_CAMERA_HEIGHT = int(os.getenv("ORBIT_AUX_CAMERA_HEIGHT", "720"))
AUX_CAMERA_CENTER_CROP = float(os.getenv("ORBIT_AUX_CAMERA_CENTER_CROP", "1"))
AUX_CAMERA_AUTO_EXPOSURE = os.getenv("ORBIT_AUX_CAMERA_AUTO_EXPOSURE", "1") != "0"
AUX_CAMERA_WARMUP_SECONDS = float(os.getenv("ORBIT_AUX_CAMERA_WARMUP_SECONDS", "1.2"))
AUX_CAMERA_EXPOSURE = os.getenv("ORBIT_AUX_CAMERA_EXPOSURE")
AUX_RELEASE_REALSENSE = os.getenv("ORBIT_AUX_RELEASE_REALSENSE", "1") != "0"
LOG_DIR = Path(__file__).resolve().parent / "logs"
# ===========================================

class IntelligentScanner:
    def __init__(
        self,
        ollama_model=None,
        ollama_api_url=None,
        ollama_timeout=None,
        ollama_num_gpu=None,
        ollama_num_ctx=None,
        ollama_num_predict=None,
        ai_provider=None,
        ai_api_base=None,
        ai_api_key=None,
    ):
        print("⚡ 初始化: RealSense + Rembg + Open3D(RANSAC)...")
        self.ollama_model = ollama_model or OLLAMA_MODEL
        self.ollama_api_url = ollama_api_url or OLLAMA_API_URL
        self.ai_provider = (ai_provider or AI_PROVIDER or "ollama").strip().lower()
        self.ai_api_base = (ai_api_base or AI_API_BASE or "").strip()
        self.ai_api_key = (ai_api_key or AI_API_KEY or "").strip()
        self.ollama_timeout = ollama_timeout or OLLAMA_TIMEOUT
        self.ollama_num_gpu = ollama_num_gpu if ollama_num_gpu is not None else OLLAMA_NUM_GPU
        self.ollama_num_ctx = ollama_num_ctx or OLLAMA_NUM_CTX
        self.ollama_num_predict = ollama_num_predict or OLLAMA_NUM_PREDICT
        self.ai_image_max_size = AI_IMAGE_MAX_SIZE
        self.ai_image_jpeg_quality = AI_IMAGE_JPEG_QUALITY
        self.segment_max_size = SEGMENT_MAX_SIZE
        self.ollama_keep_alive = OLLAMA_KEEP_ALIVE
        self.scale_roi = SCALE_ROI
        self.scale_depth_delta_mm = SCALE_DEPTH_DELTA_MM
        self.scale_ai_rotate = SCALE_AI_ROTATE
        self.scale_object_min_area_ratio = SCALE_OBJECT_MIN_AREA_RATIO
        self.scale_object_max_area_ratio = SCALE_OBJECT_MAX_AREA_RATIO
        self.scale_object_max_width_ratio = SCALE_OBJECT_MAX_WIDTH_RATIO
        self.scale_object_max_height_ratio = SCALE_OBJECT_MAX_HEIGHT_RATIO
        self.scale_object_pad = SCALE_OBJECT_PAD
        self.exposure_retry = EXPOSURE_RETRY
        self.exposure_target_mean = EXPOSURE_TARGET_MEAN
        self.exposure_low_mean = EXPOSURE_LOW_MEAN
        self.exposure_high_mean = EXPOSURE_HIGH_MEAN
        self.exposure_highlight_p98 = EXPOSURE_HIGHLIGHT_P98
        self.exposure_clip_ratio = EXPOSURE_CLIP_RATIO
        self.exposure_settle_frames = EXPOSURE_SETTLE_FRAMES
        self.ai_enhance = AI_ENHANCE
        self.aux_camera_enabled = AUX_CAMERA_ENABLED
        self.aux_camera_index = AUX_CAMERA_INDEX
        self.aux_camera_backend = AUX_CAMERA_BACKEND
        self.aux_camera_width = AUX_CAMERA_WIDTH
        self.aux_camera_height = AUX_CAMERA_HEIGHT
        self.aux_camera_center_crop = AUX_CAMERA_CENTER_CROP
        self.aux_camera_auto_exposure = AUX_CAMERA_AUTO_EXPOSURE
        self.aux_camera_warmup_seconds = AUX_CAMERA_WARMUP_SECONDS
        self.aux_camera_exposure = AUX_CAMERA_EXPOSURE
        self.aux_release_realsense = AUX_RELEASE_REALSENSE
        self.last_aux_image = None
        self.last_aux_image_path = None
        self.ai_composite_view = AI_COMPOSITE_VIEW
        self.last_measurement_meta = {}
        self.color_sensor = None
        self.pipeline = rs.pipeline()
        self.config = rs.config()
        # 保持高分辨率
        self.config.enable_stream(rs.stream.depth, 1280, 720, rs.format.z16, 30)
        self.config.enable_stream(rs.stream.color, 1920, 1080, rs.format.bgr8, 30)
        self.align = rs.align(rs.stream.color)

        try:
            self.profile = self.pipeline.start(self.config)
            self.pipeline_running = True
        except Exception as e:
            self.pipeline_running = False
            raise RuntimeError(f"RealSense 启动失败: {e}") from e
        self.depth_scale = self.profile.get_device().first_depth_sensor().get_depth_scale()

        # 滤波器
        self.spatial = rs.spatial_filter()
        self.temporal = rs.temporal_filter()

        # 获取内参 (用于 Open3D 点云生成)
        profile = self.profile.get_stream(rs.stream.color)
        intr = profile.as_video_stream_profile().get_intrinsics()
        self.color_width = intr.width
        self.color_height = intr.height
        self.color_fx = intr.fx
        self.color_fy = intr.fy
        self.color_ppx = intr.ppx
        self.color_ppy = intr.ppy
        self.color_sensor = self._find_color_sensor()
        self.last_exposure_roi = None
        self.exposure_roi_supported = EXPOSURE_ROI
        self.exposure_roi_warning_printed = False
        self._prepare_color_auto_exposure("scale", intr.width, intr.height)

    def image_to_base64(self, image):
        """将图像转换为 Base64，同时压缩分辨率以防 Token 溢出"""
        # 1. 缩放: 本地 4GB 显卡很容易被视觉 token 拖慢，入库识别优先保留主体而不是高分辨率细节。
        h, w = image.shape[:2]
        max_size = int(self.ai_image_max_size or 0)
        if max_size > 0 and max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # 2. 转 RGB (去除 Alpha 通道)
        if len(image.shape) == 3 and image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        # 3. 编码
        quality = int(np.clip(self.ai_image_jpeg_quality, 60, 95))
        _, buffer = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return base64.b64encode(buffer).decode('utf-8')

    def _openai_chat_endpoint(self):
        provider = (getattr(self, "ai_provider", "ollama") or "ollama").strip().lower()
        base = (getattr(self, "ai_api_base", "") or "").strip().rstrip("/")
        if not base:
            if provider == "gemini":
                base = "https://generativelanguage.googleapis.com/v1beta/openai"
            else:
                base = "https://api.openai.com/v1"
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _openai_content_from_images(self, user_prompt, images):
        content = [{"type": "text", "text": user_prompt}]
        for image_b64 in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                }
            )
        return content

    def _cloud_model_name(self):
        model = str(self.ollama_model or "").strip()
        provider = (getattr(self, "ai_provider", "ollama") or "ollama").strip().lower()
        if provider == "gemini" and model.lower().startswith("models/"):
            return model.split("/", 1)[1]
        return model

    @staticmethod
    def _extract_json_text(ai_response):
        if not ai_response:
            return None
        clean_json = str(ai_response).replace("```json", "").replace("```", "").strip()
        try:
            json.loads(clean_json)
            return clean_json
        except json.JSONDecodeError:
            start = clean_json.find("{")
            end = clean_json.rfind("}")
            if start >= 0 and end > start:
                candidate = clean_json[start : end + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    return None
        return None

    def _center_crop_image(self, image, ratio):
        ratio = float(np.clip(ratio, 0.25, 1.0))
        h, w = image.shape[:2]
        crop_w = int(w * ratio)
        crop_h = int(h * ratio)
        x1 = max(0, (w - crop_w) // 2)
        y1 = max(0, (h - crop_h) // 2)
        return image[y1 : y1 + crop_h, x1 : x1 + crop_w]

    def _resize_to_height(self, image, target_h):
        h, w = image.shape[:2]
        if h <= 0:
            return image
        scale = target_h / h
        return cv2.resize(image, (max(1, int(w * scale)), target_h), interpolation=cv2.INTER_AREA)

    def _resize_to_fit(self, image, max_w, max_h, allow_upscale=False):
        h, w = image.shape[:2]
        if h <= 0 or w <= 0:
            return image
        scale = min(max_w / w, max_h / h)
        if not allow_upscale:
            scale = min(scale, 1.0)
        if abs(scale - 1.0) < 0.01:
            return image
        return cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)

    def _resize_for_segmentation(self, image):
        max_size = int(getattr(self, "segment_max_size", 0) or 0)
        if max_size <= 0:
            return image, 1.0, 1.0
        h, w = image.shape[:2]
        if max(h, w) <= max_size:
            return image, 1.0, 1.0
        scale = max_size / max(h, w)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        return resized, w / new_w, h / new_h

    def _detail_image_for_ai(self, image):
        if image is None:
            return None
        work = image.copy()
        stats = self._brightness_stats(work)
        if self._highlight_at_risk(stats):
            work = self._protect_highlights(work, stats)
        elif stats["mean"] < max(self.exposure_target_mean, 58.0):
            work, _ = self._enhance_crop_for_ai(work, stats)

        lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.4, tileGridSize=(8, 8))
        l_channel = clahe.apply(l_channel)
        work = cv2.cvtColor(cv2.merge((l_channel, a_channel, b_channel)), cv2.COLOR_LAB2BGR)
        blur = cv2.GaussianBlur(work, (0, 0), 1.0)
        return cv2.addWeighted(work, 1.28, blur, -0.28, 0)

    def _panel(self, image, label):
        panel = image.copy()
        cv2.rectangle(panel, (0, 0), (panel.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(panel, label, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (210, 245, 230), 1, cv2.LINE_AA)
        return panel

    def _mark_selection_on_context(self, image, selection_bbox):
        context = image.copy()
        if not selection_bbox:
            return context
        pixels = self._normalized_bbox_to_pixels(selection_bbox, context.shape[1], context.shape[0])
        if not pixels:
            return context
        x, y, w_box, h_box = pixels
        overlay = context.copy()
        cv2.rectangle(overlay, (0, 0), (context.shape[1], context.shape[0]), (0, 0, 0), -1)
        cv2.rectangle(overlay, (x, y), (x + w_box, y + h_box), (0, 0, 0), -1)
        context = cv2.addWeighted(context, 1.0, overlay, 0.18, 0)
        cv2.rectangle(context, (x, y), (x + w_box, y + h_box), (70, 255, 135), 5)
        cv2.circle(context, (x, y), 10, (70, 255, 135), -1)
        cv2.circle(context, (x + w_box, y + h_box), 10, (70, 255, 135), -1)
        cv2.putText(
            context,
            "TARGET",
            (max(6, x), max(24, y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (70, 255, 135),
            2,
            cv2.LINE_AA,
        )
        return context

    def _make_selection_evidence_image(self, selected_crop, context_image=None, selection_bbox=None, aux_image=None):
        panels = []
        if context_image is not None:
            context = self._mark_selection_on_context(context_image, selection_bbox)
            context = self._resize_to_fit(self._detail_image_for_ai(context), 980, 560)
            panels.append(self._panel(context, "FULL TOP VIEW - TARGET IN GREEN BOX"))

        if aux_image is not None:
            aux = self._center_crop_image(aux_image, self.aux_camera_center_crop if hasattr(self, "aux_camera_center_crop") else 1.0)
            aux = self._resize_to_fit(self._detail_image_for_ai(aux), 980, 420)
            panels.append(self._panel(aux, "SIDE VIEW - CENTER SUBJECT"))

        if selected_crop is not None:
            detail = self._resize_to_fit(self._detail_image_for_ai(selected_crop), 980, 340, allow_upscale=True)
            panels.append(self._panel(detail, "GREEN BOX SUBJECT VIEW"))

        if not panels:
            return selected_crop

        gap = 10
        canvas_w = max(panel.shape[1] for panel in panels)
        canvas_h = sum(panel.shape[0] for panel in panels) + gap * (len(panels) - 1)
        canvas = np.full((canvas_h, canvas_w, 3), 18, dtype=np.uint8)
        y = 0
        for panel in panels:
            canvas[y : y + panel.shape[0], 0 : panel.shape[1]] = panel
            y += panel.shape[0] + gap
        return canvas

    def _make_ai_composite(self, main_image, aux_image):
        """Build one AI image with side-view subject dominant and top-view as context."""
        aux_focus = self._resize_to_height(self._center_crop_image(aux_image, 0.55), 420)
        main_focus = self._resize_to_height(self._center_crop_image(main_image, 0.62), 220)
        gap = 8
        canvas_h = max(aux_focus.shape[0], main_focus.shape[0])
        canvas_w = aux_focus.shape[1] + gap + main_focus.shape[1]
        canvas = np.full((canvas_h, canvas_w, 3), 18, dtype=np.uint8)

        canvas[: aux_focus.shape[0], : aux_focus.shape[1]] = aux_focus
        y = max(0, (canvas_h - main_focus.shape[0]) // 2)
        x = aux_focus.shape[1] + gap
        canvas[y : y + main_focus.shape[0], x : x + main_focus.shape[1]] = main_focus
        return canvas

    def _find_color_sensor(self):
        for sensor in self.profile.get_device().query_sensors():
            try:
                if sensor.get_info(rs.camera_info.name) == "RGB Camera":
                    return sensor
            except Exception:
                continue
        return None

    def _prepare_color_auto_exposure(self, mode, img_w, img_h):
        if not self.color_sensor:
            return

        try:
            if self.color_sensor.supports(rs.option.enable_auto_exposure):
                self.color_sensor.set_option(rs.option.enable_auto_exposure, 1)
            if self.color_sensor.supports(rs.option.auto_exposure_priority):
                self.color_sensor.set_option(rs.option.auto_exposure_priority, 0)
            if self.color_sensor.supports(rs.option.enable_auto_white_balance):
                self.color_sensor.set_option(rs.option.enable_auto_white_balance, 1)
        except Exception as exc:
            print(f"⚠️ RGB 自动曝光设置失败: {exc}")

        if self.exposure_roi_supported:
            try:
                if mode == "scale":
                    roi_x, roi_y, roi_w, roi_h = self._parse_roi(self.scale_roi, img_w, img_h)
                else:
                    roi_w, roi_h = int(img_w * 0.55), int(img_h * 0.55)
                    roi_x, roi_y = (img_w - roi_w) // 2, (img_h - roi_h) // 2
                self._set_color_exposure_roi(roi_x, roi_y, roi_w, roi_h)
            except Exception as exc:
                self.exposure_roi_supported = False
                if not self.exposure_roi_warning_printed:
                    print(f"⚠️ RGB 曝光 ROI 被相机固件拒绝，已退回主体亮度重拍: {exc}")
                    self.exposure_roi_warning_printed = True

    def _set_color_exposure_roi(self, x, y, w_box, h_box):
        if not self.color_sensor:
            return
        roi_tuple = (int(x), int(y), int(x + w_box), int(y + h_box))
        if roi_tuple == self.last_exposure_roi:
            return

        roi_sensor = self.color_sensor.as_roi_sensor()
        region = roi_sensor.get_region_of_interest()
        region.min_x, region.min_y, region.max_x, region.max_y = roi_tuple
        roi_sensor.set_region_of_interest(region)
        self.last_exposure_roi = roi_tuple
        print(
            "📷 RGB 自动曝光 ROI: "
            f"x={region.min_x}, y={region.min_y}, "
            f"w={region.max_x - region.min_x}, h={region.max_y - region.min_y}"
        )

    @staticmethod
    def _normalized_bbox_to_pixels(bbox, img_w, img_h):
        if not bbox:
            return None
        x, y, w_box, h_box = bbox
        x1 = int(round(float(x) * img_w))
        y1 = int(round(float(y) * img_h))
        x2 = int(round((float(x) + float(w_box)) * img_w))
        y2 = int(round((float(y) + float(h_box)) * img_h))
        x1 = max(0, min(x1, img_w - 1))
        y1 = max(0, min(y1, img_h - 1))
        x2 = max(x1 + 1, min(x2, img_w))
        y2 = max(y1 + 1, min(y2, img_h))
        return x1, y1, x2 - x1, y2 - y1

    def _crop_by_normalized_bbox(self, image, bbox):
        if image is None or not bbox:
            return image
        img_h, img_w = image.shape[:2]
        pixels = self._normalized_bbox_to_pixels(bbox, img_w, img_h)
        if not pixels:
            return image
        x, y, w_box, h_box = pixels
        return image[y : y + h_box, x : x + w_box]

    def _display_bbox_to_sensor_bbox(self, bbox, mode):
        if not bbox:
            return None
        x, y, w_box, h_box = [float(v) for v in bbox]
        angle = (self.scale_ai_rotate if mode == "scale" else 0) % 360
        if angle == 90:
            return y, 1.0 - x - w_box, h_box, w_box
        if angle == 180:
            return 1.0 - x - w_box, 1.0 - y - h_box, w_box, h_box
        if angle == 270:
            return 1.0 - y - h_box, x, h_box, w_box
        return x, y, w_box, h_box

    def _prepare_selection_exposure_roi(self, bbox, mode="auto"):
        if not self.exposure_roi_supported or not self.color_sensor or not bbox:
            return False
        try:
            sensor_bbox = self._display_bbox_to_sensor_bbox(bbox, mode)
            pixels = self._normalized_bbox_to_pixels(sensor_bbox, self.color_width, self.color_height)
            if not pixels:
                return False
            x, y, w_box, h_box = pixels
            self._set_color_exposure_roi(x, y, w_box, h_box)
            return True
        except Exception as exc:
            self.exposure_roi_supported = False
            if not self.exposure_roi_warning_printed:
                print(f"⚠️ 框选曝光 ROI 被相机固件拒绝，已退回主体亮度重拍: {exc}")
                self.exposure_roi_warning_printed = True
            return False

    def _wait_aligned_frame(self):
        frames = self.pipeline.wait_for_frames()
        aligned_frames = self.align.process(frames)
        color_frame = aligned_frames.get_color_frame()
        depth_frame = aligned_frames.get_depth_frame()
        return color_frame, depth_frame

    def process_selection_frame(self, color_frame, depth_frame, mode, selection_bbox):
        """Fast path for manual ROI confirmation.

        Once the user has confirmed the target box, running semantic
        segmentation again is wasted time. This path keeps the full image for
        AI context, estimates scale from depth at the selected box, and lets
        the caller crop the exact ROI later.
        """
        filtered_depth = self.spatial.process(depth_frame)
        filtered_depth = self.temporal.process(filtered_depth)
        depth_frame = filtered_depth.as_depth_frame()

        full_color = np.asanyarray(color_frame.get_data())
        full_depth = np.asanyarray(depth_frame.get_data())
        h_img, w_img = full_color.shape[:2]
        display_image = self._rotate_for_ai(full_color, mode)
        display_h, display_w = display_image.shape[:2]

        sensor_bbox = self._display_bbox_to_sensor_bbox(selection_bbox, mode)
        pixels = self._normalized_bbox_to_pixels(sensor_bbox, w_img, h_img)
        if not pixels:
            self.last_measurement_meta = {
                "measurement_reliable": False,
                "measurement_source": "manual_bbox_fast_no_bbox",
                "selection_view": "full_d435i",
                "segmentation_skipped": True,
            }
            return display_image, None, None

        x, y, w_box, h_box = pixels
        obj_depth_crop = full_depth[y : y + h_box, x : x + w_box]
        valid_depths = obj_depth_crop[obj_depth_crop > 0]
        if len(valid_depths) == 0:
            valid_depths = full_depth[full_depth > 0]

        width_mm = None
        height_mm = None
        meta = {
            "measurement_reliable": False,
            "measurement_source": "manual_bbox_fast_depth",
            "selection_bbox": {
                "x": int(round(selection_bbox[0] * display_w)),
                "y": int(round(selection_bbox[1] * display_h)),
                "w": int(round(selection_bbox[2] * display_w)),
                "h": int(round(selection_bbox[3] * display_h)),
                "image_w": display_w,
                "image_h": display_h,
                "source": "manual_fast",
            },
            "manual_selection_bbox": {
                "x": selection_bbox[0],
                "y": selection_bbox[1],
                "w": selection_bbox[2],
                "h": selection_bbox[3],
            },
            "selection_view": "full_d435i",
            "segmentation_skipped": True,
        }

        if len(valid_depths) > 0:
            reference_depth = np.percentile(valid_depths, 20) * self.depth_scale
            intrinsics = color_frame.profile.as_video_stream_profile().get_intrinsics()
            p_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [x, y], reference_depth)
            p_br = rs.rs2_deproject_pixel_to_point(intrinsics, [x + w_box, y + h_box], reference_depth)
            p_img_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [0, 0], reference_depth)
            p_img_br = rs.rs2_deproject_pixel_to_point(intrinsics, [w_img, h_img], reference_depth)

            width_mm = abs(p_br[0] - p_tl[0]) * 1000
            height_mm = abs(p_br[1] - p_tl[1]) * 1000
            frame_w_mm = abs(p_img_br[0] - p_img_tl[0]) * 1000
            frame_h_mm = abs(p_img_br[1] - p_img_tl[1]) * 1000
            angle = (self.scale_ai_rotate if mode == "scale" else 0) % 360
            if angle in (90, 270):
                frame_w_mm, frame_h_mm = frame_h_mm, frame_w_mm
            meta.update(
                {
                    "selection_frame_width_mm": frame_w_mm,
                    "selection_frame_height_mm": frame_h_mm,
                    "selection_reference_depth_m": reference_depth,
                }
            )

        self.last_measurement_meta = meta
        print(
            "⏱️ 框选快路径: 跳过语义分割"
            + (f", size={width_mm:.1f}x{height_mm:.1f}mm" if width_mm and height_mm else "")
        )
        return display_image, width_mm, height_mm

    def _release_realsense_for_aux(self):
        if not getattr(self, "pipeline_running", False):
            return
        try:
            self.pipeline.stop()
            self.pipeline_running = False
            print("📷 主相机已释放，准备采集辅助视角")
        except Exception as exc:
            print(f"⚠️ 主相机释放失败，仍尝试采集辅助视角: {exc}")

    def _warm_camera(self, frames=None):
        for _ in range(frames if frames is not None else self.exposure_settle_frames):
            self.pipeline.wait_for_frames()

    @staticmethod
    def _brightness_stats(image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        valid = gray[gray > 5]
        if len(valid) == 0:
            valid = gray.reshape(-1)
        return {
            "mean": float(np.mean(valid)),
            "p50": float(np.percentile(valid, 50)),
            "p90": float(np.percentile(valid, 90)),
            "p98": float(np.percentile(valid, 98)),
            "clip_ratio": float(np.mean(gray >= 245)),
        }

    def _should_retry_exposure(self, stats):
        if self._highlight_at_risk(stats):
            return False
        return stats["mean"] < self.exposure_low_mean or stats["mean"] > self.exposure_high_mean

    def _highlight_at_risk(self, stats):
        return (
            stats.get("p98", 0.0) >= self.exposure_highlight_p98
            or stats.get("clip_ratio", 0.0) >= self.exposure_clip_ratio
        )

    def _try_manual_exposure_adjustment(self, stats):
        if not self.color_sensor or not self.exposure_retry:
            return False

        mean = max(stats["mean"], 1.0)
        factor = self.exposure_target_mean / mean
        if factor > 1.0 and self._highlight_at_risk(stats):
            print(
                "⚠️ 检测到白色标签/高光接近过曝，跳过增曝光重拍: "
                f"mean={stats['mean']:.1f}, p98={stats['p98']:.1f}, clip={stats['clip_ratio']*100:.2f}%"
            )
            return False
        factor = float(np.clip(factor, 0.35, 2.4))
        if 0.88 <= factor <= 1.12:
            return False

        try:
            if self.color_sensor.supports(rs.option.enable_auto_exposure):
                self.color_sensor.set_option(rs.option.enable_auto_exposure, 0)

            changed = False
            if self.color_sensor.supports(rs.option.exposure):
                exposure_range = self.color_sensor.get_option_range(rs.option.exposure)
                current_exposure = self.color_sensor.get_option(rs.option.exposure)
                new_exposure = float(np.clip(current_exposure * factor, exposure_range.min, exposure_range.max))
                self.color_sensor.set_option(rs.option.exposure, new_exposure)
                changed = abs(new_exposure - current_exposure) >= max(1.0, exposure_range.step)

            if factor > 2.2 and self.color_sensor.supports(rs.option.gain):
                gain_range = self.color_sensor.get_option_range(rs.option.gain)
                current_gain = self.color_sensor.get_option(rs.option.gain)
                gain_factor = min(1.45, factor / 1.8)
                new_gain = float(np.clip(current_gain * gain_factor, gain_range.min, gain_range.max))
                self.color_sensor.set_option(rs.option.gain, new_gain)
                changed = changed or abs(new_gain - current_gain) >= max(1.0, gain_range.step)

            if changed:
                print(
                    "🔁 主体亮度偏离目标，重拍一次: "
                    f"mean={stats['mean']:.1f}, p90={stats['p90']:.1f}, "
                    f"p98={stats['p98']:.1f}, clip={stats['clip_ratio']*100:.2f}%, factor={factor:.2f}"
                )
            return changed
        except Exception as exc:
            print(f"⚠️ 手动曝光重拍设置失败: {exc}")
            return False

    def _enhance_crop_for_ai(self, image, stats):
        protected = self._protect_highlights(image, stats)
        if protected is not image:
            return protected, True

        if not self.ai_enhance or stats["mean"] >= self.exposure_low_mean:
            return image, False

        target_p90 = max(82.0, self.exposure_target_mean * 1.45)
        alpha = float(np.clip(target_p90 / max(stats["p90"], 1.0), 1.05, 2.15))
        bright = cv2.convertScaleAbs(image, alpha=alpha, beta=6)

        lab = cv2.cvtColor(bright, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
        l_channel = clahe.apply(l_channel)
        enhanced = cv2.cvtColor(cv2.merge((l_channel, a_channel, b_channel)), cv2.COLOR_LAB2BGR)
        return enhanced, True

    def _protect_highlights(self, image, stats):
        if not self._highlight_at_risk(stats):
            return image

        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        l_float = l_channel.astype(np.float32)
        mask = l_float > 205
        l_float[mask] = 205 + (l_float[mask] - 205) * 0.38
        l_float = np.clip(l_float, 0, 238).astype(np.uint8)
        compressed = cv2.cvtColor(cv2.merge((l_float, a_channel, b_channel)), cv2.COLOR_LAB2BGR)
        print(
            "🛡️ 已压缩标签高光: "
            f"p98={stats['p98']:.1f}, clip={stats['clip_ratio']*100:.2f}%"
        )
        return compressed

    def capture_auxiliary_view(self):
        """Capture the optional auxiliary side view.

        The side view is treated as a centered visual hint only. Weight and
        dimensions still come from the scale and RealSense path.
        """
        if not self.aux_camera_enabled:
            return None, None

        for cap, used_index, used_backend in self._open_aux_camera_candidates():
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.aux_camera_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.aux_camera_height)
                if self.aux_camera_auto_exposure:
                    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
                elif self.aux_camera_exposure is not None:
                    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
                    cap.set(cv2.CAP_PROP_EXPOSURE, float(self.aux_camera_exposure))

                frame = None
                best_frame = None
                best_score = None
                warmup_frames = 36 if self.aux_camera_auto_exposure else 10
                max_frames = 60 if self.aux_camera_auto_exposure else warmup_frames
                deadline = time.time() + max(0.0, self.aux_camera_warmup_seconds)
                for index in range(max_frames):
                    ok, candidate = cap.read()
                    if ok and candidate is not None:
                        frame = candidate
                        if self.aux_camera_auto_exposure and index >= warmup_frames // 3:
                            cropped = self._center_crop_aux(candidate)
                            candidate_stats = self._brightness_stats(cropped)
                            score = self._aux_frame_score(candidate_stats)
                            if best_score is None or score < best_score:
                                best_score = score
                                best_frame = candidate
                    if index + 1 >= warmup_frames and time.time() >= deadline:
                        break
                if frame is None:
                    print(f"⚠️ 辅助相机无有效帧，继续尝试下一个: index={used_index}, backend={used_backend}")
                    continue

                if best_frame is not None:
                    frame = best_frame
                stats = self._brightness_stats(frame)
                frame = self._protect_highlights(frame, stats)

                LOG_DIR.mkdir(exist_ok=True)
                path = LOG_DIR / f"aux_camera_{int(time.time())}.jpg"
                cv2.imwrite(str(path), frame)
                print(
                    "📷 辅助相机 辅助视角已采集: "
                    f"{path.name}, mean={stats['mean']:.1f}, p98={stats['p98']:.1f}, "
                    f"clip={stats['clip_ratio']*100:.2f}%, index={used_index}, backend={used_backend}"
                )
                return frame, path
            except Exception as exc:
                print(f"⚠️ 辅助视角采集失败，继续尝试下一个: index={used_index}, backend={used_backend}, {exc}")
            finally:
                cap.release()
        return None, None

    def _open_aux_camera_candidates(self):
        errors = []
        for index in self._aux_camera_index_candidates():
            for backend_name, backend in self._aux_camera_backend_candidates():
                try:
                    cap = cv2.VideoCapture(index, backend)
                    if cap.isOpened():
                        print(f"📷 辅助相机打开: index={index}, backend={backend_name}")
                        yield cap, index, backend_name
                        continue
                    cap.release()
                    errors.append(f"{index}/{backend_name}")
                except Exception as exc:
                    errors.append(f"{index}/{backend_name}: {exc}")
        if errors:
            print(f"⚠️ 辅助视角未打开，已尝试 {', '.join(errors[:12])}")

    def _aux_camera_index_candidates(self):
        value = str(self.aux_camera_index or "0").strip().lower()
        if value in {"", "auto"}:
            return [0, 1, 2, 3, 4, 5]
        try:
            return [int(value)]
        except ValueError:
            return [0, 1, 2, 3, 4, 5]

    def _aux_camera_backend_candidates(self):
        backends = {
            "dshow": cv2.CAP_DSHOW,
            "msmf": cv2.CAP_MSMF,
            "any": cv2.CAP_ANY,
        }
        value = str(self.aux_camera_backend or "auto").strip().lower()
        if value in backends:
            return [(value, backends[value])]
        return [("dshow", cv2.CAP_DSHOW), ("any", cv2.CAP_ANY), ("msmf", cv2.CAP_MSMF)]

    def _center_crop_aux(self, image):
        ratio = float(np.clip(self.aux_camera_center_crop, 0.35, 1.0))
        h, w = image.shape[:2]
        crop_w = int(w * ratio)
        crop_h = int(h * ratio)
        x1 = max(0, (w - crop_w) // 2)
        y1 = max(0, (h - crop_h) // 2)
        return image[y1 : y1 + crop_h, x1 : x1 + crop_w]

    def _aux_frame_score(self, stats):
        target_mean = 112.0
        mean_penalty = abs(stats["mean"] - target_mean) * 0.8
        dark_penalty = max(0.0, 64.0 - stats["mean"]) * 2.0
        highlight_penalty = max(0.0, stats["p98"] - 245.0) * 1.8
        clip_penalty = stats["clip_ratio"] * 1600.0
        return mean_penalty + dark_penalty + highlight_penalty + clip_penalty

    def ask_ollama(self, image, width_mm, height_mm, weight_g=None, labels=None, locations=None, mode="auto", aux_image=None, context_image=None, selection_bbox=None):
        """使用标准的 Chat 接口发送请求"""
        display_model = self._cloud_model_name() if (getattr(self, "ai_provider", "ollama") or "ollama").strip().lower() != "ollama" else self.ollama_model
        print(f"🚀 正在发送给 {display_model} (Chat Mode)...")

        image_rule = "图像是俯视主视角，只识别裁图中央或秤盘上的小物品。"
        aux_context = "无"
        if context_image is not None:
            ai_image = self._make_selection_evidence_image(image, context_image, selection_bbox=selection_bbox, aux_image=aux_image)
            images = [self.image_to_base64(ai_image)]
            aux_context = "有。AI 输入是一张证据合成图：第一块是完整俯视图且完整目标物在绿色框内；若存在辅助相机，第二块是同一物品的侧视中心主体；最后一块是绿色框内完整目标物的框选目标视图。"
            image_rule = "图像是同一物品的证据合成图：绿色框内就是完整待入库物品；优先识别完整俯视图绿色框内的目标物，并用侧视中心主体确认同一物品的形状、厚度和结构；框选目标视图展示的是绿色框内的完整目标物，用于看清该目标物及其标签、文字、接口、材质等细节；不要把相机、电子秤、秤盘、桌面或背景作为入库物品。"
            print(f"🧩 AI 输入使用框选证据合成图: {ai_image.shape[1]}x{ai_image.shape[0]}")
        elif aux_image is not None and self.ai_composite_view:
            ai_image = self._make_ai_composite(image, aux_image)
            images = [self.image_to_base64(ai_image)]
            aux_context = "有。AI 输入为合成单图：左侧大图是侧面主体，右侧小图是俯视补充。"
            image_rule = "图像左侧大图是待入库物品侧面主体，右侧小图只是俯视补充；拍摄设备本身不是入库物品。"
            print(f"🧩 AI 输入使用辅助主体合成图: {ai_image.shape[1]}x{ai_image.shape[0]}")
        elif aux_image is not None:
            images = [self.image_to_base64(image), self.image_to_base64(aux_image)]
            aux_context = "有。第二张图是固定侧面辅助视角；拍摄设备不是入库物品。待入库物品位于第二张图中央。"
            image_rule = "第一张图是俯视主视角，第二张图只是侧面辅助视角；拍摄设备本身不是入库物品。"
        else:
            images = [self.image_to_base64(image)]
        image_payload_mb = sum(len(item) * 3 / 4 for item in images) / (1024 * 1024)
        print(f"⏱️ AI 图像载荷: {len(images)} 张, 约 {image_payload_mb:.2f} MB")
        label_context = json.dumps(labels[:40], ensure_ascii=False) if labels else "[]"
        location_context = json.dumps(locations[:40], ensure_ascii=False) if locations else "[]"
        weight_context = f"{weight_g:.1f}g" if weight_g is not None else "未读取"
        if width_mm and height_mm and width_mm > 0 and height_mm > 0:
            size_context = f"{width_mm:.1f}mm x {height_mm:.1f}mm"
        else:
            size_context = "未可靠测得"

        scale_rule = "忽略电子秤、秤盘、托盘、显示屏和桌面，只识别秤盘上的被称重物品。" if mode == "scale" else "只识别裁图中央主体，忽略背景和承载物。"
        prompt = f"""
只输出一个 JSON 对象，第一字符必须是 {{。
任务: {scale_rule}
图像: {image_rule}
数据: 尺寸约 {size_context}；电子秤重量 {weight_context}；辅助视角 {aux_context}。
识别策略:
- 先判断框选区域或画面中央的主体，再用辅助视角补充确认同一件物品的外观。
- name/category/manufacturer/model 只根据可见外观、清楚可读文字、标志、部件关系和传感器数据填写。
- 输出语言必须以简体中文为主体：name、category、description、reasoning 必须使用简体中文；如果需要引用可见英文品牌、型号或标记，只能放在 manufacturer/model 或描述证据中作为原文引用。
- name 应是中文通用物品名，不要直接使用英文商品短语；品牌、厂商、系列词只应写入 manufacturer/model 或描述证据，不要把品牌当作物品类别。
- category 是表单里的主分类：优先从“可用 Homebox 标签”中选择最合适的一个，并且必须逐字复制；只有没有任何合适既有标签时，才输出一个简短的中文大类词。不要输出 "Electronics"、"Measuring Instruments" 等英文类别，不要自造细碎分类路径。
- manufacturer 用来保存清楚可见的品牌、厂商或制造商，例如 "Guanglu"、"Canon"。
- model 只用来保存清楚可见的真实型号、焦段、规格、量程、编号或关键标记，例如 "RF 28-70"、"0-150mm"、"CD-15AX"；如果只看见品牌而没有型号，则 model=null，不能把品牌写进 model。
- 可读文字、logo、接口、按键、材质、形状、相互连接关系等多种证据应综合考虑；不要只凭单一轮廓猜具体型号或品类。
- 判断品类时优先利用能说明功能的结构证据，例如可动部件、显示/读数区域、夹持/连接/调节机构和多视角一致性；文字、刻度、颜色和材质不能单独决定品类。
- 证据不足时，使用更宽泛的名称和 category；完全无法确认时用 name="待确认物品", category="待确认", model=null。
- 重量只能使用电子秤字段，不要从图片/OCR/秤屏读取重量。
- 电子秤重量字段只表示称重传感器读数，不是物品类别证据；不要因为输入里出现“电子秤重量”就把物品识别为电子秤。
- 不要把辅助相机、电子秤、秤盘、托盘、桌面、背景杂物作为 name/category/model；也不要读取承载电子秤、秤屏或秤体上的品牌/文字来命名待入库物品。
- 不要复用示例、历史识别结果或常见物品模板；只有框内目标物自身清晰可见的结构和文字同时支持时，才填写具体品类、品牌和型号。
- 品牌、制造商、型号、焦段和编号只能来自清楚可读的文字；看不清就不要补全，禁止根据常见品牌或提示词补全。
- tags 是 Homebox 的业务分类标签，按数组输出 1-4 个；优先直接复用已有标签，且 tags[0] 应尽量与 category 相同。
- 如果已有标签适合，必须逐字复制已有标签，不要改写、翻译、增删字符；可以多选，但不要选择与物品明显无关的标签。
- 标签应描述整件物品所属的大类，而不是内部零件、局部材质、显示屏、电池、接口或技术特征；例如带电子显示的工具仍应按工具类归类，不能只因为有电子部分就归为电子元件。
- 如果确实没有合适的已有标签，才新建一个宽泛分类标签；新标签必须以简体中文开头，可选附带英文后缀，例如“测量工具Measuring Tools”“电子设备Electronics”；不要输出纯英文标签，不要把品牌、型号、系列、具体品名、位置、尺寸或重量写进 tags。
- suggested_location 是 Homebox 位置字段：只能从“已有位置”数组中选择一个字符串，并且必须逐字复制；如果没有合适位置或数组为空，必须写 null。
- suggested_location 绝对不能新建、改写、翻译、拼接路径或猜测位置名；不要把 category、tags、英文类别、物品类型、桌面/电子秤/现场位置写成位置。
- description 和 reasoning 必须简短，并说明最关键的可见证据。
- description 和 reasoning 可以说明目标物“放在电子秤上”，但证据主体必须是目标物自身的结构、标识和部件；不要写成“根据电子秤外观判断”。
可用 Homebox 标签(JSON数组，复用时必须逐字等于其中一个字符串): {label_context}
已有位置(JSON数组，复用时必须逐字等于其中一个字符串): {location_context}
字段: name, category, description, size, manufacturer, model, tags, suggested_location, reasoning。
"""

        system_prompt = (
            "你是 O.R.B.I.T. 入库识别器。必须只输出一个合法 JSON 对象，"
            "第一字符必须是 {，不要输出 Markdown、解释、思考过程或空内容。"
            "除 manufacturer/model 中的品牌、型号、规格原文以及逐字复用的既有标签/位置外，"
            "所有自然语言字段必须以简体中文为主体。"
        )
        json_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "category": {"type": "string"},
                "description": {"type": "string", "maxLength": 60},
                "size": {"type": ["string", "null"], "maxLength": 48},
                "manufacturer": {"type": ["string", "null"], "maxLength": 64},
                "model": {"type": ["string", "null"], "maxLength": 64},
                "tags": {"type": "array", "items": {"type": "string", "maxLength": 40}, "minItems": 1, "maxItems": 4},
                "suggested_location": {"type": ["string", "null"], "maxLength": 48},
                "reasoning": {"type": "string", "maxLength": 80},
            },
            "required": ["name", "category", "description", "size", "manufacturer", "model", "tags", "suggested_location", "reasoning"],
        }

        def build_ollama_payload(user_prompt, num_predict):
            payload = {
                "model": self.ollama_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": user_prompt,
                        "images": images,
                    },
                ],
                "stream": False,
                "format": json_schema,
                "keep_alive": str(getattr(self, "ollama_keep_alive", OLLAMA_KEEP_ALIVE) or "15m"),
                "options": {
                    "temperature": 0.0,
                    "num_ctx": self.ollama_num_ctx,
                    "num_predict": num_predict,
                    "top_k": 1,
                },
                "think": False,
            }
            if self.ollama_num_gpu is not None and str(self.ollama_num_gpu).strip() != "":
                payload["options"]["num_gpu"] = int(self.ollama_num_gpu)
            return payload

        def build_openai_payload(
            user_prompt,
            num_predict,
            response_format="json_schema",
            include_reasoning=True,
            include_temperature=True,
            token_parameter=None,
        ):
            provider_name = (getattr(self, "ai_provider", "ollama") or "ollama").strip().lower()
            token_parameter = token_parameter or (
                "max_completion_tokens" if provider_name == "openai" else "max_tokens"
            )
            payload = {
                "model": self._cloud_model_name(),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": self._openai_content_from_images(user_prompt, images)},
                ],
                token_parameter: int(num_predict),
            }
            if include_temperature:
                payload["temperature"] = 0
            if provider_name == "gemini" and include_reasoning:
                payload["reasoning_effort"] = "low"
            if response_format == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "orbit_item",
                        "schema": json_schema,
                        "strict": False,
                    },
                }
            elif response_format == "json_object":
                payload["response_format"] = {"type": "json_object"}
            return payload

        def cloud_error(response):
            try:
                error = (response.json() or {}).get("error") or {}
                if isinstance(error, dict):
                    return str(error.get("param") or "").lower(), str(error.get("message") or "").lower()
            except (TypeError, ValueError):
                pass
            return "", str(getattr(response, "text", "") or "").lower()

        def cloud_parameter_rejected(response, parameter):
            if response.status_code not in {400, 422}:
                return False
            error_param, error_message = cloud_error(response)
            parameter = str(parameter or "").lower()
            return error_param == parameter or (
                parameter in error_message
                and any(marker in error_message for marker in ("unsupported", "not supported", "unknown parameter"))
            )

        def cloud_response_format_rejected(response, response_format):
            if response.status_code not in {400, 422} or not response_format:
                return False
            error_param, error_message = cloud_error(response)
            return error_param == "response_format" or any(
                marker in error_message
                for marker in ("response_format", str(response_format).lower())
            )

        def cloud_response_format(provider_name, attempt_index):
            if provider_name == "gemini":
                return "json_object" if attempt_index == 0 else None
            return "json_schema" if attempt_index == 0 else "json_object"

        def cloud_temperature_enabled(provider_name):
            if provider_name != "openai":
                return True
            model_name = self._cloud_model_name().strip().lower()
            return not model_name.startswith(("gpt-5", "o1", "o3", "o4"))

        def extract_openai_content(result):
            choices = result.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(str(item.get("text") or ""))
                    else:
                        parts.append(str(item))
                return "\n".join(part for part in parts if part).strip()
            return str(content).strip()

        def openai_finish_reason(result):
            choices = result.get("choices") or []
            if not choices:
                return ""
            return str(choices[0].get("finish_reason") or "")

        try:
            num_predict = int(self.ollama_num_predict)
            provider = (getattr(self, "ai_provider", "ollama") or "ollama").strip().lower()
            print(f"⏳ 等待 AI 响应 ({provider}, num_predict={num_predict})...")
            start_t = time.time()

            if provider == "ollama":
                payload = build_ollama_payload(prompt, num_predict)
                response = requests.post(self.ollama_api_url, json=payload, timeout=self.ollama_timeout)
                print(f"⏱️ 网络耗时: {time.time() - start_t:.2f}s")

                if response.status_code != 200:
                    print(f"❌ API 报错: {response.status_code}")
                    print(f"❌ 错误详情: {response.text}")
                    return None

                result = response.json()
                if "message" in result:
                    message = result.get("message") or {}
                    content = message.get("content") or ""
                    thinking = message.get("thinking") or ""
                elif "response" in result:
                    content = result.get("response") or ""
                    thinking = result.get("thinking") or ""
                else:
                    print(f"⚠️ 响应结构极其异常: {result}")
                    return None
            else:
                api_key = (getattr(self, "ai_api_key", "") or "").strip()
                if not api_key:
                    print("❌ 云端 AI 未配置 API Key")
                    return None
                endpoint = self._openai_chat_endpoint()
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                if CLOUD_MAX_TOKENS > 0:
                    cloud_num_predict = CLOUD_MAX_TOKENS
                elif provider == "gemini":
                    cloud_num_predict = max(num_predict * 8, 8192)
                else:
                    cloud_num_predict = max(num_predict * 4, 2048)
                content = ""
                last_error_text = ""
                retry_prompt = prompt

                def send_cloud_request(active_prompt, max_output_tokens, response_format):
                    token_parameter = "max_completion_tokens" if provider == "openai" else "max_tokens"
                    include_temperature = cloud_temperature_enabled(provider)
                    include_reasoning = True

                    def send():
                        request_payload = build_openai_payload(
                            active_prompt,
                            max_output_tokens,
                            response_format=response_format,
                            include_reasoning=include_reasoning,
                            include_temperature=include_temperature,
                            token_parameter=token_parameter,
                        )
                        request_response = requests.post(
                            endpoint,
                            headers=headers,
                            json=request_payload,
                            timeout=self.ollama_timeout,
                        )
                        return request_response, request_payload

                    response, payload = send()
                    if cloud_parameter_rejected(response, token_parameter):
                        replacement = (
                            "max_tokens" if token_parameter == "max_completion_tokens" else "max_completion_tokens"
                        )
                        print(f"⚠️ 云端接口不接受 {token_parameter}，改用 {replacement} 重试...")
                        token_parameter = replacement
                        response, payload = send()
                    if cloud_parameter_rejected(response, "temperature"):
                        print("⚠️ 当前模型不接受 temperature，移除后重试...")
                        include_temperature = False
                        response, payload = send()
                    if cloud_parameter_rejected(response, "reasoning_effort") and "reasoning_effort" in payload:
                        print("⚠️ 云端接口不接受 reasoning_effort，移除后重试...")
                        include_reasoning = False
                        response, payload = send()
                    return response, payload

                for attempt in range(2):
                    attempt_num_predict = cloud_num_predict if attempt == 0 else max(cloud_num_predict, 12288 if provider == "gemini" else 4096)
                    response_format = cloud_response_format(provider, attempt)
                    active_response_format = response_format
                    response, payload = send_cloud_request(
                        retry_prompt,
                        attempt_num_predict,
                        active_response_format,
                    )
                    if cloud_response_format_rejected(response, response_format):
                        print(f"⚠️ 云端接口不接受 {response_format}，改用纯提示词 JSON 请求...")
                        active_response_format = None
                        response, payload = send_cloud_request(
                            retry_prompt,
                            attempt_num_predict,
                            active_response_format,
                        )

                    if response.status_code != 200:
                        last_error_text = response.text
                        break

                    result = response.json()
                    content = extract_openai_content(result)
                    finish_reason = openai_finish_reason(result)
                    if finish_reason:
                        print(f"⏱️ 云端 finish_reason: {finish_reason}")
                    json_text = self._extract_json_text(content)
                    if json_text:
                        content = json_text
                        break

                    print(f"⚠️ 云端返回非 JSON，片段: {content[:160]}")
                    retry_prompt = (
                        "重新输出完整 JSON 对象，第一字符必须是 {，最后字符必须是 }。"
                        "不要写 Here is、说明、Markdown、代码围栏或任何 JSON 外文本。"
                        "字段必须只有: name, category, description, size, manufacturer, model, tags, suggested_location, reasoning。"
                        "category 优先逐字复用可用 Homebox 标签；tags 优先逐字复用已有标签，没有合适标签时才按现有标签风格新建。"
                        "suggested_location 只能逐字复制已有位置，没有匹配则 null。"
                        f"可用 Homebox 标签: {label_context}。已有位置: {location_context}。"
                    )
                print(f"⏱️ 网络耗时: {time.time() - start_t:.2f}s")

                if last_error_text:
                    print(f"❌ API 报错: {response.status_code}")
                    print(f"❌ 错误详情: {last_error_text}")
                    return None

                thinking = ""

            if content.strip():
                return content

            if thinking:
                print("⚠️ AI 只返回了 thinking，使用本地结构化兜底，片段如下：")
                print(thinking[:500])
                return json.dumps(
                    self._fallback_json_from_thinking(thinking, width_mm, height_mm, weight_g),
                    ensure_ascii=False,
                )

            print("⚠️ AI content 为空")
            return None

        except Exception as e:
            print(f"❌ 连接/处理错误: {e}")
            return None

    def _fallback_json_from_thinking(self, thinking, width_mm, height_mm, weight_g=None):
        size = None
        if width_mm and height_mm and 5 <= width_mm <= 2000 and 5 <= height_mm <= 2000:
            size = f"{width_mm:.1f}mm x {height_mm:.1f}mm"

        reasoning = f"{self.ollama_model} 仅返回 thinking 未返回 content；程序不再根据 thinking 关键词推断品类，已生成待人工确认草稿。"
        if weight_g is not None:
            reasoning += f" 重量来自电子秤读数 {weight_g:.1f}g。"

        return {
            "name": "待确认物品",
            "category": "待确认",
            "description": "模型未返回最终 JSON，已生成待人工确认的入库草稿。",
            "size": size,
            "model": None,
            "tags": ["待确认"],
            "suggested_location": None,
            "reasoning": reasoning,
        }

    @staticmethod
    def parse_ai_json(ai_response):
        if not ai_response:
            return None
        extracted = IntelligentScanner._extract_json_text(ai_response)
        if extracted:
            return json.loads(extracted)
        clean_json = ai_response.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean_json)
        except json.JSONDecodeError:
            start = clean_json.find("{")
            end = clean_json.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(clean_json[start : end + 1])
                except json.JSONDecodeError:
                    pass
        print("⚠️ AI 未返回可解析 JSON，原始内容如下：")
        print(ai_response)
        return None

    @staticmethod
    def normalize_ai_data(data, labels=None, locations=None):
        if not isinstance(data, dict):
            return data

        required = {
            "name": "待确认物品",
            "category": "待确认",
            "description": "",
            "size": None,
            "manufacturer": None,
            "model": None,
            "tags": [],
            "suggested_location": None,
            "reasoning": "",
        }
        normalized = {**required, **data}

        for key in ("name", "category", "description", "size", "manufacturer", "model", "suggested_location", "reasoning"):
            value = normalized.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value.lower() in {"unknown", "unk", "n/a", "na", "none", "null"} or value in {"未知", "不明", "无"}:
                    value = None if key in {"size", "manufacturer", "model", "suggested_location"} else ""
                normalized[key] = value

        if not normalized.get("manufacturer") and normalized.get("model"):
            model = str(normalized.get("model") or "").strip()
            evidence = " ".join(
                str(normalized.get(key) or "") for key in ("description", "reasoning", "name")
            )
            if model and re.search(rf"(品牌|厂商|制造商|牌子|brand|maker|manufacturer)\s*(?:为|是|[:：])?\s*{re.escape(model)}", evidence, re.I):
                normalized["manufacturer"] = model
                normalized["model"] = None

        tags = normalized.get("tags")
        if isinstance(tags, str):
            tags = [part.strip() for part in re.split(r"[,，、/;；\n]+", tags) if part.strip()]
        elif not isinstance(tags, list):
            tags = []
        normalized["tags"] = IntelligentScanner._clean_homebox_tags(
            [str(tag).strip() for tag in tags if str(tag).strip()],
            normalized,
        )[:4]
        if labels is not None:
            allowed_labels = {
                str(item.get("name") if isinstance(item, dict) else item).strip()
                for item in (labels or [])
                if str(item.get("name") if isinstance(item, dict) else item).strip()
            }
            exact_tags = [tag for tag in normalized["tags"] if tag in allowed_labels]
            if exact_tags:
                normalized["tags"] = exact_tags[:4]
        if normalized["tags"]:
            category = str(normalized.get("category") or "").strip()
            tag_keys = {IntelligentScanner._tag_key(tag) for tag in normalized["tags"]}
            category_key = IntelligentScanner._tag_key(category)
            if not category or category_key not in tag_keys:
                normalized["category"] = normalized["tags"][0]

        location = normalized.get("suggested_location")
        if isinstance(location, str) and re.search(r"电子秤|秤盘|托盘|桌面|玻璃|现场|当前位置|turntable|platter|table", location, re.I):
            normalized["suggested_location"] = None
        elif locations is not None:
            allowed_locations = {
                str(item.get("name") if isinstance(item, dict) else item).strip()
                for item in (locations or [])
                if str(item.get("name") if isinstance(item, dict) else item).strip()
            }
            if str(location or "").strip() not in allowed_locations:
                normalized["suggested_location"] = None
            else:
                normalized["suggested_location"] = str(location).strip()

        return normalized

    @staticmethod
    def _dedupe_text(values):
        result = []
        seen = set()
        for value in values:
            if value in seen:
                continue
            result.append(value)
            seen.add(value)
        return result

    @staticmethod
    def _tag_key(value):
        return re.sub(r"[\s,，、/;；:：|\\()（）\[\]【】<>\-_.+]+", "", str(value or "").casefold())

    @classmethod
    def _clean_homebox_tags(cls, tags, data):
        blocked = {
            "general",
            "misc",
            "other",
            "unknown",
            "待确认",
            "其他",
            "通用",
            "一般",
            "ai识别",
            "orbit",
            "o.r.b.i.t.",
        }
        name_key = cls._tag_key(data.get("name"))
        model_key = cls._tag_key(data.get("model"))
        result = []
        seen = set()
        for tag in tags:
            key = cls._tag_key(tag)
            if not key or key in seen or key in blocked:
                continue
            if key == model_key or key == name_key:
                continue
            if model_key and len(key) >= 3 and (key in model_key or model_key in key):
                continue
            if name_key and len(key) >= 4 and key in name_key:
                continue
            result.append(tag)
            seen.add(key)
        return result

    def capture_object(self, mode="auto", weight_g=None, selection_bbox=None):
        """Capture one aligned frame and return the cropped inventory subject."""
        self._prepare_color_auto_exposure(mode, self.color_width, self.color_height)
        selection_roi_applied = False
        if selection_bbox:
            selection_roi_applied = self._prepare_selection_exposure_roi(selection_bbox, mode=mode)
        self._warm_camera()

        def capture_processed():
            start_t = time.time()
            color_frame, depth_frame = self._wait_aligned_frame()
            if not color_frame or not depth_frame:
                return None, None, None
            if selection_bbox:
                result = self.process_selection_frame(color_frame, depth_frame, mode, selection_bbox)
                print(f"⏱️ 框选拍摄处理耗时: {time.time() - start_t:.2f}s")
                return result
            result = self.process_frame(color_frame, depth_frame, mode=mode)
            print(f"⏱️ 拍摄分割处理耗时: {time.time() - start_t:.2f}s")
            return result

        self.last_measurement_meta = {}
        crop_img, width, height = capture_processed()
        if crop_img is None:
            return None, None

        stats_image = self._crop_by_normalized_bbox(crop_img, selection_bbox) if selection_bbox else crop_img
        stats = self._brightness_stats(stats_image)
        retried = False
        if self._should_retry_exposure(stats) and self._try_manual_exposure_adjustment(stats):
            retried = True
            self._warm_camera()
            retry_crop, retry_width, retry_height = capture_processed()
            if retry_crop is not None:
                retry_stats_image = self._crop_by_normalized_bbox(retry_crop, selection_bbox) if selection_bbox else retry_crop
                retry_stats = self._brightness_stats(retry_stats_image)
                old_score = abs(stats["mean"] - self.exposure_target_mean)
                new_score = abs(retry_stats["mean"] - self.exposure_target_mean)
                retry_safe = not self._highlight_at_risk(retry_stats)
                old_safe = not self._highlight_at_risk(stats)
                if (retry_safe and (new_score <= old_score or retry_stats["mean"] > stats["mean"])) or (
                    not old_safe and retry_stats["p98"] <= stats["p98"]
                ):
                    crop_img, width, height, stats = retry_crop, retry_width, retry_height, retry_stats
                    print(
                        "✅ 重拍亮度: "
                        f"mean={stats['mean']:.1f}, p90={stats['p90']:.1f}, "
                        f"p98={stats['p98']:.1f}, clip={stats['clip_ratio']*100:.2f}%"
                    )
                else:
                    print(
                        "↩️ 重拍图高光风险更高，保留原裁图: "
                        f"retry p98={retry_stats['p98']:.1f}, clip={retry_stats['clip_ratio']*100:.2f}%"
                    )

        crop_img, enhanced = self._enhance_crop_for_ai(crop_img, stats)
        final_stats_image = self._crop_by_normalized_bbox(crop_img, selection_bbox) if selection_bbox else crop_img
        final_stats = self._brightness_stats(final_stats_image)
        if enhanced:
            print(
                "✨ 已对 AI 裁图做轻量亮度增强: "
                f"mean {stats['mean']:.1f} -> {final_stats['mean']:.1f}"
            )

        if self.aux_camera_enabled and self.aux_release_realsense:
            self._release_realsense_for_aux()
        aux_img, aux_path = self.capture_auxiliary_view()
        self.last_aux_image = aux_img
        self.last_aux_image_path = aux_path

        measurement = {
            "width_mm": width,
            "height_mm": height,
            "weight_g": weight_g,
            "mode": mode,
            "exposure_selection_bbox": {"x": selection_bbox[0], "y": selection_bbox[1], "w": selection_bbox[2], "h": selection_bbox[3]} if selection_bbox else None,
            "exposure_selection_roi_applied": selection_roi_applied,
            "brightness_mean": final_stats["mean"],
            "brightness_p90": final_stats["p90"],
            "brightness_p98": final_stats["p98"],
            "clip_ratio": final_stats["clip_ratio"],
            "exposure_retried": retried,
            "image_enhanced": enhanced,
            "aux_camera": "aux" if aux_img is not None else None,
            "aux_image": str(aux_path) if aux_path else None,
        }
        measurement.update(self.last_measurement_meta or {})
        return crop_img, measurement

    def capture_and_identify(self, mode="auto", weight_g=None, labels=None, locations=None):
        """Capture one aligned frame, measure the object, and ask Ollama."""
        crop_img, measurement = self.capture_object(mode=mode, weight_g=weight_g)
        if crop_img is None or not measurement:
            return None, None, None

        width = measurement["width_mm"]
        height = measurement["height_mm"]
        if width and height:
            print(f"✅ 测量完成: {width:.1f}x{height:.1f}mm")
        else:
            print("✅ 拍摄完成: 尺寸未可靠测得")
        ai_response = self.ask_ollama(
            crop_img,
            width,
            height,
            weight_g=weight_g,
            labels=labels,
            locations=locations,
            mode=mode,
            aux_image=self.last_aux_image,
        )
        data = self.parse_ai_json(ai_response)
        if not data:
            return None, crop_img, measurement

        data = self.normalize_ai_data(data, labels=labels, locations=locations)
        print(json.dumps(data, indent=4, ensure_ascii=False))
        return data, crop_img, measurement

    def select_central_object(self, mask, center_x, center_y):
        """
        核心算法：从杂乱的 Mask 中，找出那个就在“准星”位置的物体
        """
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours: return None, None

        best_cnt = None
        min_dist = float('inf')

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 1000: continue # 忽略噪点

            # 1. 检查是否包含中心点 (点在轮廓内)
            # dist > 0 表示在内部，dist < 0 表示在外部
            dist_to_point = cv2.pointPolygonTest(cnt, (center_x, center_y), True)

            if dist_to_point >= 0:
                # 命中！中心点就在这个物体内部，它肯定是主角
                return cnt, cv2.boundingRect(cnt)

            # 2. 如果中心点没在任何物体内（比如物体是环形的，或者稍微偏了一点）
            # 计算轮廓重心到画面中心的距离
            M = cv2.moments(cnt)
            if M["m00"] != 0:
                cX = int(M["m10"] / M["m00"])
                cY = int(M["m01"] / M["m00"])
                dist = np.sqrt((cX - center_x)**2 + (cY - center_y)**2)

                if dist < min_dist:
                    min_dist = dist
                    best_cnt = cnt

        if best_cnt is not None:
            return best_cnt, cv2.boundingRect(best_cnt)
        return None, None

    def get_largest_cluster_bbox(self, mask):
        """辅助函数：找 Mask 里最大的连通区域的包围盒"""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours: return None
        # 简单的逻辑：找所有轮廓的大包围盒 (合并所有碎片)
        # 对于椅子，我们要把腿、座、背都包进去
        min_x, min_y = 9999, 9999
        max_x, max_y = 0, 0
        found = False
        for cnt in contours:
            if cv2.contourArea(cnt) < 500: continue # 忽略噪点
            x, y, w, h = cv2.boundingRect(cnt)
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x + w)
            max_y = max(max_y, y + h)
            found = True

        if not found: return None
        return (min_x, min_y, max_x - min_x, max_y - min_y)

    def get_scale_object_bbox(self, mask, roi_w, roi_h):
        """Find the small raised object on the scale pan, not the pan itself."""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        roi_area = max(1, roi_w * roi_h)
        min_area = max(120, roi_area * self.scale_object_min_area_ratio)
        max_area = roi_area * self.scale_object_max_area_ratio
        max_w = roi_w * self.scale_object_max_width_ratio
        max_h = roi_h * self.scale_object_max_height_ratio
        center_x, center_y = roi_w * 0.5, roi_h * 0.5
        candidates = []

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            bbox_area = w * h
            if bbox_area > max_area or w > max_w or h > max_h:
                continue

            touches_border = x <= 2 or y <= 2 or x + w >= roi_w - 2 or y + h >= roi_h - 2
            if touches_border and bbox_area > roi_area * 0.025:
                continue

            aspect = w / max(1, h)
            if aspect < 0.25 or aspect > 4.5:
                continue

            c_x = x + w * 0.5
            c_y = y + h * 0.5
            dist = np.hypot((c_x - center_x) / roi_w, (c_y - center_y) / roi_h)
            area_ratio = bbox_area / roi_area
            useful_size_bonus = min(area_ratio / 0.035, 1.0) * 0.22
            border_penalty = 0.18 if touches_border else 0.0
            score = dist + area_ratio * 0.35 + border_penalty - useful_size_bonus
            candidates.append((score, (x, y, w, h), cnt))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0])
        best_x, best_y, best_w, best_h = candidates[0][1]

        # Merge nearby fragments of the same object, such as sparse depth on lens/label.
        margin = max(18, int(max(best_w, best_h) * 0.9), int(min(roi_w, roi_h) * 0.045))
        union_x1 = best_x
        union_y1 = best_y
        union_x2 = best_x + best_w
        union_y2 = best_y + best_h
        expanded = (
            max(0, best_x - margin),
            max(0, best_y - margin),
            min(roi_w, best_x + best_w + margin),
            min(roi_h, best_y + best_h + margin),
        )

        for _, (x, y, w, h), _ in candidates[1:]:
            c_x = x + w * 0.5
            c_y = y + h * 0.5
            if expanded[0] <= c_x <= expanded[2] and expanded[1] <= c_y <= expanded[3]:
                union_x1 = min(union_x1, x)
                union_y1 = min(union_y1, y)
                union_x2 = max(union_x2, x + w)
                union_y2 = max(union_y2, y + h)

        union_w = union_x2 - union_x1
        union_h = union_y2 - union_y1
        if union_w <= 0 or union_h <= 0:
            return None
        if union_w > max_w or union_h > max_h or union_w * union_h > max_area:
            return best_x, best_y, best_w, best_h
        return union_x1, union_y1, union_w, union_h

    def get_scale_color_bbox(self, roi_color):
        """Fallback for black objects that produce sparse RealSense depth."""
        gray = cv2.cvtColor(roi_color, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        dark_limit = min(95, max(35, np.percentile(blur, 18) + 10))
        dark_mask = (blur <= dark_limit).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, kernel)
        dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        h, w = dark_mask.shape[:2]
        return self.get_scale_object_bbox(dark_mask, w, h)

    def get_visual_foreground_bbox(self, image, prefer_center=True):
        """Use the existing rembg foreground mask to suggest a subject bbox."""
        try:
            original_h, original_w = image.shape[:2]
            work_image, scale_x, scale_y = self._resize_for_segmentation(image)
            color_rgb = cv2.cvtColor(work_image, cv2.COLOR_BGR2RGB)
            start_t = time.time()
            no_bg_image = _remove_background(color_rgb)
            if work_image.shape[:2] != image.shape[:2]:
                print(
                    "⏱️ rembg 前景分割: "
                    f"{work_image.shape[1]}x{work_image.shape[0]} -> {time.time() - start_t:.2f}s"
                )
            alpha_channel = no_bg_image[:, :, 3]
            _, mask = cv2.threshold(alpha_channel, 10, 255, cv2.THRESH_BINARY)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            min_area = max(120, work_image.shape[0] * work_image.shape[1] * 0.0015)
            contours = [cnt for cnt in contours if cv2.contourArea(cnt) >= min_area]
            if not contours:
                full_mask = cv2.resize(mask, (original_w, original_h), interpolation=cv2.INTER_NEAREST)
                return None, full_mask

            cnt = None
            if prefer_center:
                center_x = work_image.shape[1] // 2
                center_y = work_image.shape[0] // 2
                cnt, _bbox = self.select_central_object(mask, center_x, center_y)
                if cnt is not None and cv2.contourArea(cnt) < min_area:
                    cnt = None
            if cnt is None:
                cnt = max(contours, key=cv2.contourArea)

            clean_mask = np.zeros_like(mask)
            cv2.drawContours(clean_mask, [cnt], -1, 255, thickness=cv2.FILLED)
            x, y, w_box, h_box = cv2.boundingRect(cnt)
            bx = max(0, min(int(round(x * scale_x)), original_w - 1))
            by = max(0, min(int(round(y * scale_y)), original_h - 1))
            bw = max(1, min(int(round(w_box * scale_x)), original_w - bx))
            bh = max(1, min(int(round(h_box * scale_y)), original_h - by))
            full_mask = cv2.resize(clean_mask, (original_w, original_h), interpolation=cv2.INTER_NEAREST)
            return (bx, by, bw, bh), full_mask
        except Exception as exc:
            print(f"⚠️ 视觉前景分割失败: {exc}")
            return None, None

    @staticmethod
    def _bbox_score_for_center(bbox, frame_w, frame_h):
        x, y, w, h = bbox
        center_dist = np.hypot((x + w * 0.5 - frame_w * 0.5) / max(1, frame_w), (y + h * 0.5 - frame_h * 0.5) / max(1, frame_h))
        area_ratio = (w * h) / max(1, frame_w * frame_h)
        useful_size_bonus = min(area_ratio / 0.18, 1.0) * 0.22
        return center_dist - useful_size_bonus

    def process_with_geometry(self, color_image, depth_image):
        """
        【家具模式 v3.0】RANSAC 地面剔除 + DBSCAN 聚类 + 3D中心锁定
        """
        o3d = _open3d()
        h_full, w_full = depth_image.shape

        # 1. 降采样 (480x270)
        target_w, target_h = 480, 270
        small_depth = cv2.resize(depth_image, (target_w, target_h), interpolation=cv2.INTER_NEAREST)

        ratio_x = target_w / w_full
        ratio_y = target_h / h_full

        small_intr = o3d.camera.PinholeCameraIntrinsic(
            target_w, target_h,
            self.color_fx * ratio_x,
            self.color_fy * ratio_y,
            self.color_ppx * ratio_x,
            self.color_ppy * ratio_y
        )

        o3d_depth = o3d.geometry.Image(small_depth)
        pcd = o3d.geometry.PointCloud.create_from_depth_image(
            o3d_depth, small_intr, depth_scale=1.0/self.depth_scale, depth_trunc=4.0, stride=1
        )

        if len(pcd.points) < 500: return None, None, None

        # 2. 去噪 & 切地板
        pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=10, std_ratio=1.5)
        plane_model, inliers = pcd.segment_plane(distance_threshold=0.05, ransac_n=3, num_iterations=100)
        objects_pcd = pcd.select_by_index(inliers, invert=True)

        if len(objects_pcd.points) < 100: return None, None, None

        # 3. DBSCAN 聚类
        labels = np.array(objects_pcd.cluster_dbscan(eps=0.20, min_points=50, print_progress=False))
        if len(labels) == 0: return None, None, None

        max_label = labels.max()
        if max_label < 0: return None, None, None

        # 4. 【核心升级】寻找离画面中心最近的物体
        # RealSense 坐标系：X=右, Y=下, Z=前。
        # 画面中心就是 X=0, Y=0 的轴线。

        all_points = np.asarray(objects_pcd.points)
        best_label = -1
        min_dist_to_center = float('inf')

        # 遍历所有聚类结果
        for label in range(max_label + 1):
            # 提取当前物体的点
            indices = np.where(labels == label)[0]
            if len(indices) < 200: continue # 忽略太小的噪点团

            cluster_points = all_points[indices]

            # 计算 3D 重心 (Centroid)
            centroid = np.mean(cluster_points, axis=0) # [x, y, z]

            # 计算重心到光轴(Z轴)的距离：只看 X 和 Y 的偏移量
            # dist = sqrt(x^2 + y^2)
            dist_to_axis = np.sqrt(centroid[0]**2 + centroid[1]**2)

            # 选最近的
            if dist_to_axis < min_dist_to_center:
                min_dist_to_center = dist_to_axis
                best_label = label

        if best_label == -1: return None, None, None

        # 提取最佳物体
        chair_indices = np.where(labels == best_label)[0]
        chair_pcd = objects_pcd.select_by_index(chair_indices)

        # 5. 投影回 2D 计算包围盒
        pts = np.asarray(chair_pcd.points)
        fx = small_intr.intrinsic_matrix[0,0]
        fy = small_intr.intrinsic_matrix[1,1]
        cx = small_intr.intrinsic_matrix[0,2]
        cy = small_intr.intrinsic_matrix[1,2]

        z = pts[:, 2]
        u = (pts[:, 0] * fx / z) + cx
        v = (pts[:, 1] * fy / z) + cy

        min_u, max_u = np.min(u), np.max(u)
        min_v, max_v = np.min(v), np.max(v)

        scale_x = w_full / target_w
        scale_y = h_full / target_h

        x = int(max(0, min_u * scale_x))
        y = int(max(0, min_v * scale_y))
        w_box = int(min(w_full, max_u * scale_x) - x)
        h_box = int(min(h_full, max_v * scale_y) - y)

        # 6. 计算尺寸
        aabb = chair_pcd.get_axis_aligned_bounding_box()
        extent = aabb.get_extent()
        real_w_mm = extent[0] * 1000
        real_h_mm = extent[1] * 1000

        # 7. 裁切 RGB
        pad = 40
        final_crop = color_image[
            max(0, y-pad):min(h_full, y+h_box+pad),
            max(0, x-pad):min(w_full, x+w_box+pad)
        ]

        return final_crop, real_w_mm, real_h_mm

    def _parse_roi(self, roi_value, img_w, img_h):
        """Parse x,y,w,h from pixel values or fractional image coordinates."""
        fallback = (int(img_w * 0.49), int(img_h * 0.22), int(img_w * 0.34), int(img_h * 0.48))
        try:
            parts = [float(p) for p in re.split(r"[,;\s]+", roi_value.strip()) if p]
            if len(parts) != 4:
                raise ValueError("ROI needs four numbers")

            if all(0 <= p <= 1 for p in parts):
                x, y, w_box, h_box = (
                    int(parts[0] * img_w),
                    int(parts[1] * img_h),
                    int(parts[2] * img_w),
                    int(parts[3] * img_h),
                )
            else:
                x, y, w_box, h_box = [int(round(p)) for p in parts]
        except Exception as exc:
            print(f"⚠️ ORBIT_SCALE_ROI 无效，使用默认秤盘区域: {exc}")
            x, y, w_box, h_box = fallback

        x = max(0, min(x, img_w - 1))
        y = max(0, min(y, img_h - 1))
        w_box = max(1, min(w_box, img_w - x))
        h_box = max(1, min(h_box, img_h - y))
        return x, y, w_box, h_box

    def _rotate_for_ai(self, image, mode):
        angle = self.scale_ai_rotate if mode == "scale" else 0
        angle = angle % 360
        if angle == 90:
            return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        if angle == 180:
            return cv2.rotate(image, cv2.ROTATE_180)
        if angle == 270:
            return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return image

    def _rotate_bbox_for_ai(self, bbox, mode):
        angle = (self.scale_ai_rotate if mode == "scale" else 0) % 360
        x = bbox["x"]
        y = bbox["y"]
        w = bbox["w"]
        h = bbox["h"]
        image_w = bbox["image_w"]
        image_h = bbox["image_h"]
        rotated = dict(bbox)
        if angle == 90:
            rotated.update({"x": image_h - y - h, "y": x, "w": h, "h": w, "image_w": image_h, "image_h": image_w})
        elif angle == 180:
            rotated.update({"x": image_w - x - w, "y": image_h - y - h})
        elif angle == 270:
            rotated.update({"x": y, "y": image_w - x - w, "w": h, "h": w, "image_w": image_h, "image_h": image_w})
        return rotated

    def process_scale_frame(self, full_color, full_depth, color_frame):
        """Return the full view plus a candidate box for the scale workflow.

        Reflective trays, sparse depth, cluttered desks, and bright labels can
        each break a single detector. For a top-down scale bench, collect
        visual, depth, and color candidates, then score them with general
        geometry instead of treating any one source as absolute.
        """
        h_img, w_img = full_color.shape[:2]
        roi_x, roi_y, roi_w, roi_h = self._parse_roi(self.scale_roi, w_img, h_img)
        roi_color = full_color[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]
        roi_depth = full_depth[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]

        candidates = []
        bbox_source = "full_roi"
        valid_roi_depths = roi_depth[roi_depth > 0]

        def clamp_global_bbox(x, y, w_box, h_box):
            x = int(round(x))
            y = int(round(y))
            w_box = int(round(w_box))
            h_box = int(round(h_box))
            x = max(0, min(x, w_img - 1))
            y = max(0, min(y, h_img - 1))
            w_box = max(1, min(w_box, w_img - x))
            h_box = max(1, min(h_box, h_img - y))
            return x, y, w_box, h_box

        def add_candidate(source, bbox, origin="roi"):
            if not bbox:
                return
            x, y, w_box, h_box = bbox
            if origin == "roi":
                x += roi_x
                y += roi_y
            x, y, w_box, h_box = clamp_global_bbox(x, y, w_box, h_box)
            area_ratio = (w_box * h_box) / max(1, w_img * h_img)
            if area_ratio < 0.0006:
                return

            score = self._bbox_score_for_center((x, y, w_box, h_box), w_img, h_img)
            roi_cx = roi_x + roi_w * 0.5
            roi_cy = roi_y + roi_h * 0.5
            cand_cx = x + w_box * 0.5
            cand_cy = y + h_box * 0.5
            roi_dist = np.hypot((cand_cx - roi_cx) / max(1, w_img), (cand_cy - roi_cy) / max(1, h_img))
            score += roi_dist * 0.65

            ix1 = max(x, roi_x)
            iy1 = max(y, roi_y)
            ix2 = min(x + w_box, roi_x + roi_w)
            iy2 = min(y + h_box, roi_y + roi_h)
            overlap = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            overlap_ratio = overlap / max(1, w_box * h_box)
            if overlap_ratio <= 0.05:
                score += 0.28
            else:
                score -= min(overlap_ratio, 1.0) * 0.08

            if area_ratio > 0.42:
                score += 0.50
            elif area_ratio > 0.26:
                score += 0.22

            if source == "visual_full":
                score -= 0.18
            elif source == "depth_object":
                score -= 0.04
            elif source == "color_object":
                score += 0.04

            candidates.append((score, source, (x, y, w_box, h_box)))

        visual_bbox, _visual_mask = self.get_visual_foreground_bbox(full_color, prefer_center=True)
        add_candidate("visual_full", visual_bbox, origin="full")

        if len(valid_roi_depths) > 100:
            near_depth = np.percentile(valid_roi_depths, 15)
            delta_units = (self.scale_depth_delta_mm / 1000.0) / self.depth_scale
            depth_mask = ((roi_depth > 0) & (roi_depth <= near_depth + delta_units)).astype(np.uint8) * 255
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
            depth_mask = cv2.morphologyEx(depth_mask, cv2.MORPH_OPEN, kernel)
            depth_mask = cv2.morphologyEx(depth_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            bbox = self.get_scale_object_bbox(depth_mask, roi_w, roi_h)
            add_candidate("depth_object", bbox, origin="roi")

        add_candidate("color_object", self.get_scale_color_bbox(roi_color), origin="roi")

        roi_visual_bbox, _roi_visual_mask = self.get_visual_foreground_bbox(roi_color, prefer_center=True)
        add_candidate("visual_roi", roi_visual_bbox, origin="roi")

        if candidates:
            candidates.sort(key=lambda item: item[0])
            _score, bbox_source, (global_x, global_y, final_w, final_h) = candidates[0]
            measurement_reliable = True
        else:
            global_x, global_y, final_w, final_h = roi_x, roi_y, roi_w, roi_h
            measurement_reliable = False

        display_image = self._rotate_for_ai(full_color, "scale")
        selection_bbox = None
        if bbox_source != "full_roi":
            selection_bbox = {
                "x": global_x,
                "y": global_y,
                "w": final_w,
                "h": final_h,
                "image_w": w_img,
                "image_h": h_img,
                "source": bbox_source,
            }
            selection_bbox = self._rotate_bbox_for_ai(selection_bbox, "scale")

        obj_depth_crop = full_depth[global_y : global_y + final_h, global_x : global_x + final_w]
        valid_depths = obj_depth_crop[obj_depth_crop > 0]

        if len(valid_depths) == 0 or not measurement_reliable:
            self.last_measurement_meta = {
                "measurement_reliable": False,
                "measurement_source": bbox_source,
            }
            if selection_bbox:
                self.last_measurement_meta["selection_bbox"] = selection_bbox
            if len(valid_roi_depths) > 100:
                try:
                    reference_depth = np.percentile(valid_roi_depths, 20) * self.depth_scale
                    intrinsics = color_frame.profile.as_video_stream_profile().get_intrinsics()
                    p_img_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [0, 0], reference_depth)
                    p_img_br = rs.rs2_deproject_pixel_to_point(intrinsics, [w_img, h_img], reference_depth)
                    self.last_measurement_meta.update(
                        {
                            "selection_frame_width_mm": abs(p_img_br[0] - p_img_tl[0]) * 1000,
                            "selection_frame_height_mm": abs(p_img_br[1] - p_img_tl[1]) * 1000,
                            "selection_reference_depth_m": reference_depth,
                            "selection_view": "full_d435i",
                        }
                    )
                except Exception as exc:
                    print(f"⚠️ 手动框尺寸参考计算失败: {exc}")
            return display_image, None, None

        front_depth = np.percentile(valid_depths, 20) * self.depth_scale
        intrinsics = color_frame.profile.as_video_stream_profile().get_intrinsics()
        p_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [global_x, global_y], front_depth)
        p_br = rs.rs2_deproject_pixel_to_point(
            intrinsics,
            [global_x + final_w, global_y + final_h],
            front_depth,
        )
        p_img_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [0, 0], front_depth)
        p_img_br = rs.rs2_deproject_pixel_to_point(intrinsics, [w_img, h_img], front_depth)

        real_w = abs(p_br[0] - p_tl[0]) * 1000
        real_h = abs(p_br[1] - p_tl[1]) * 1000
        frame_w_mm = abs(p_img_br[0] - p_img_tl[0]) * 1000
        frame_h_mm = abs(p_img_br[1] - p_img_tl[1]) * 1000
        self.last_measurement_meta = {
            "measurement_reliable": True,
            "measurement_source": bbox_source,
            "selection_bbox": selection_bbox,
            "selection_frame_width_mm": frame_w_mm,
            "selection_frame_height_mm": frame_h_mm,
            "selection_reference_depth_m": front_depth,
            "selection_view": "full_d435i",
        }
        print(
            "📐 秤盘主体框: "
            f"{final_w}x{final_h}px source={bbox_source}, "
            f"size={real_w:.1f}x{real_h:.1f}mm"
        )
        return display_image, real_w, real_h

    def process_frame(self, color_frame, depth_frame, mode="auto"):
        # 预处理深度
        filtered_depth = self.spatial.process(depth_frame)
        filtered_depth = self.temporal.process(filtered_depth)
        depth_frame = filtered_depth.as_depth_frame()

        full_color = np.asanyarray(color_frame.get_data())
        full_depth = np.asanyarray(depth_frame.get_data())
        h_img, w_img = full_color.shape[:2]

        # ==========================================
        # 1. 策略分发
        # ==========================================
        proc_color = full_color
        offset_x, offset_y = 0, 0
        if mode == "furniture":
            # === 新增：家具模式 (RANSAC) ===
            return self.process_with_geometry(full_color, full_depth)
        if mode == "scale":
            return self.process_scale_frame(full_color, full_depth, color_frame)
        if mode == "macro":
            # 微距模式：强行只看中间 640x480
            rw, rh = 640, 480
            rx, ry = (w_img - rw)//2, (h_img - rh)//2
            proc_color = full_color[ry:ry+rh, rx:rx+rw]
            full_depth = full_depth[ry:ry+rh, rx:rx+rw] # 深度图也切
            offset_x, offset_y = rx, ry

        bbox, mask = self.get_visual_foreground_bbox(proc_color, prefer_center=(mode == "auto"))
        if bbox is None or mask is None:
            return None, None, None
        final_x, final_y, final_w, final_h = bbox
        # ==========================================
        # 4. 深度计算 (复用之前的抗透明算法)
        # ==========================================
        # 注意：这里要处理越界保护
        d_y1 = max(0, final_y)
        d_y2 = min(proc_color.shape[0], final_y+final_h)
        d_x1 = max(0, final_x)
        d_x2 = min(proc_color.shape[1], final_x+final_w)

        obj_depth_crop = full_depth[d_y1:d_y2, d_x1:d_x2]
        obj_mask_crop = mask[d_y1:d_y2, d_x1:d_x2]

        valid_depths = obj_depth_crop[(obj_mask_crop > 0) & (obj_depth_crop > 0)]
        if len(valid_depths) == 0: return None, None, None

        sorted_depths = np.sort(valid_depths)
        p_idx = int(len(sorted_depths) * 0.20)
        front_depth = sorted_depths[p_idx] * self.depth_scale

        # ==========================================
        # 5. 计算尺寸 & 智能裁切
        # ==========================================
        intrinsics = color_frame.profile.as_video_stream_profile().get_intrinsics()

        # 还原到全局坐标
        global_x = final_x + offset_x
        global_y = final_y + offset_y

        p_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [global_x, global_y], front_depth)
        p_br = rs.rs2_deproject_pixel_to_point(intrinsics, [global_x+final_w, global_y+final_h], front_depth)
        p_img_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [0, 0], front_depth)
        p_img_br = rs.rs2_deproject_pixel_to_point(intrinsics, [w_img, h_img], front_depth)

        real_w = abs(p_br[0] - p_tl[0]) * 1000
        real_h = abs(p_br[1] - p_tl[1]) * 1000
        frame_w_mm = abs(p_img_br[0] - p_img_tl[0]) * 1000
        frame_h_mm = abs(p_img_br[1] - p_img_tl[1]) * 1000
        segmentation_bbox = {
            "x": global_x,
            "y": global_y,
            "w": final_w,
            "h": final_h,
            "image_w": w_img,
            "image_h": h_img,
            "source": f"visual_{mode}",
        }

        if mode == "auto":
            self.last_measurement_meta = {
                "measurement_reliable": True,
                "measurement_source": "visual_auto_full_view",
                "segmentation_bbox": segmentation_bbox,
                "selection_bbox": segmentation_bbox,
                "selection_frame_width_mm": frame_w_mm,
                "selection_frame_height_mm": frame_h_mm,
                "selection_reference_depth_m": front_depth,
                "selection_view": "full_d435i",
            }
            return full_color, real_w, real_h

        # 生成 AI 用的图
        # 把背景涂黑
        clean_image = proc_color.copy()
        clean_image[mask == 0] = 0

        # 自动裁切出物体 (Auto模式下这会自动放大物体；Full模式下是全图)
        pad = 20
        crop = clean_image[
            max(0, final_y-pad):min(clean_image.shape[0], final_y+final_h+pad),
            max(0, final_x-pad):min(clean_image.shape[1], final_x+final_w+pad)
        ]
        crop_h, crop_w = crop.shape[:2]
        self.last_measurement_meta = {
            "measurement_reliable": True,
            "measurement_source": f"visual_{mode}",
            "segmentation_bbox": segmentation_bbox,
            "selection_bbox": {
                "x": 0,
                "y": 0,
                "w": crop_w,
                "h": crop_h,
                "image_w": crop_w,
                "image_h": crop_h,
                "source": f"visual_{mode}_crop",
            },
        }

        return self._rotate_for_ai(crop, mode), real_w, real_h

    def run(self, on_success_callback=None):
        print("\n=== 📷 智能扫描仪 (Auto-Focus) ===")
        print(" [SPACE] 识别")
        print(" [S]     切换模式 (Scale / Auto / Full / Macro / Furniture)")

        cv2.namedWindow("Viewfinder", cv2.WINDOW_NORMAL)

        modes = ["scale", "auto", "full", "macro", "furniture"]
        preferred_mode = os.getenv("ORBIT_SCAN_MODE", "auto")
        mode_idx = modes.index(preferred_mode) if preferred_mode in modes else 0

        while True:
            # 获取帧
            frames = self.pipeline.wait_for_frames()
            aligned_frames = self.align.process(frames)
            color_frame = aligned_frames.get_color_frame()
            depth_frame = aligned_frames.get_depth_frame()
            if not color_frame: continue

            # 预览处理
            curr_image = np.asanyarray(color_frame.get_data())
            h, w = curr_image.shape[:2]
            mode_name = modes[mode_idx]

            # 绘制 UI 指示
            display_img = curr_image.copy()

            # 画一个十字准星，告诉用户“中心在这里”
            cx, cy = w//2, h//2
            cv2.line(display_img, (cx-20, cy), (cx+20, cy), (0, 255, 0), 2)
            cv2.line(display_img, (cx, cy-20), (cx, cy+20), (0, 255, 0), 2)

            if mode_name == "scale":
                roi_x, roi_y, roi_w, roi_h = self._parse_roi(self.scale_roi, w, h)
                cv2.rectangle(display_img, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h), (0, 255, 255), 2)
                cv2.putText(display_img, "SCALE PAN", (roi_x, max(30, roi_y - 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            elif mode_name == "macro":
                # Macro 模式画个红框提示范围
                rw, rh = 640, 480
                cv2.rectangle(display_img, (cx-rw//2, cy-rh//2), (cx+rw//2, cy+rh//2), (0, 0, 255), 2)
                cv2.putText(display_img, "MACRO LOCK", (cx-50, cy-rh//2-10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            elif mode_name == "auto":
                cv2.putText(display_img, "AUTO FOCUS", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            elif mode_name == "furniture":
                cv2.putText(display_img, "FURNITURE MODE", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            else:
                cv2.putText(display_img, "FULL FRAME", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2)

            # 缩放显示
            preview_img = cv2.resize(display_img, (1280, 720))
            cv2.imshow("Viewfinder", preview_img)

            key = cv2.waitKey(1) & 0xFF

            if key == 32: # SPACE
                print(f"\n📸 扫描中 ({mode_name})...")
                # 传入当前模式
                crop_img, width, height = self.process_frame(color_frame, depth_frame, mode=mode_name)

                if crop_img is not None:
                    print(f"✅ 测量完成: {width:.1f}x{height:.1f}mm")

                    # 1. 接收返回值
                    ai_response = self.ask_ollama(crop_img, width, height, mode=mode_name)

                    # 2. 打印结果
                    print("\n" + "="*40)
                    print("🤖 Qwen3-VL 识别报告:")
                    print("="*40)

                    if ai_response:
                        data = self.parse_ai_json(ai_response)
                        if data:
                            print(json.dumps(data, indent=4, ensure_ascii=False))

                            # 如果有回调，传给 main.py
                            if on_success_callback:
                                on_success_callback(data, crop_img)
                    else:
                        print("❌ AI 返回为空 (Check Ollama logs)")

                    print("="*40 + "\n")

                    cv2.imshow("Result Crop", crop_img)
                else:
                    print("⚠️ 扫描失败，未对准物体")

            elif key == ord('s'):
                mode_idx = (mode_idx + 1) % len(modes)
                print(f"🔄 切换模式: {modes[mode_idx]}")

            elif key == ord('q'):
                break

        self.pipeline.stop()
        cv2.destroyAllWindows()

    def release(self):
        try:
            if getattr(self, "pipeline_running", False):
                self.pipeline.stop()
                self.pipeline_running = False
        except Exception:
            pass

if __name__ == "__main__":
    app = IntelligentScanner()
    app.run()
