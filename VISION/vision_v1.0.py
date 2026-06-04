import pyrealsense2 as rs
import numpy as np
import cv2
import time
import os
from rembg import remove # 引入 AI 抠图库

class AiEnhancedScanner:
    def __init__(self):
        print("⚡ 初始化 RealSense + AI 分割引擎...")

        self.pipeline = rs.pipeline()
        self.config = rs.config()

        # 开启高分辨率
        self.config.enable_stream(rs.stream.depth, 1280, 720, rs.format.z16, 30)
        self.config.enable_stream(rs.stream.color, 1920, 1080, rs.format.bgr8, 30)

        self.align = rs.align(rs.stream.color)

        try:
            self.profile = self.pipeline.start(self.config)
            print("✅ 相机启动成功！")
        except Exception as e:
            print(f"❌ 相机启动失败: {e}")
            exit()

        self.depth_scale = self.profile.get_device().first_depth_sensor().get_depth_scale()

        # 滤波器：对透明物体很重要，能填补一些深度空洞
        self.spatial = rs.spatial_filter()
        self.spatial.set_option(rs.option.holes_fill, 3) # 强力填孔
        self.temporal = rs.temporal_filter()

    def scan_object(self):
        if not os.path.exists("logs"):
            os.makedirs("logs")

        # 1. 采集图像
        for _ in range(10): self.pipeline.wait_for_frames() # 预热
        frames = self.pipeline.wait_for_frames()
        aligned_frames = self.align.process(frames)

        color_frame = aligned_frames.get_color_frame()
        depth_frame = aligned_frames.get_depth_frame() # 此时是对齐到 1920x1080 的深度图

        if not color_frame or not depth_frame: return None, None

        # 2. 深度图预处理
        # 即使对齐了，深度图也是由 720P 插值上来的。
        # 我们先做滤波
        filtered_depth = self.spatial.process(depth_frame)
        filtered_depth = self.temporal.process(filtered_depth)
        depth_frame = filtered_depth.as_depth_frame()

        color_image = np.asanyarray(color_frame.get_data())
        depth_image = np.asanyarray(depth_frame.get_data())

        # ==========================================
        # 核心升级：使用 rembg (U2-Net) 进行 AI 分割
        # ==========================================
        print("🤖 AI 正在识别前景物体 (Rembg)...")

        # rembg 输入需要是 RGB (cv2 默认是 BGR)
        color_rgb = cv2.cvtColor(color_image, cv2.COLOR_BGR2RGB)

        # AI 抠图，返回 RGBA 图像 (A通道就是 Mask)
        # 这一步在 8840U 上大约耗时 0.5s - 1s
        no_bg_image = remove(color_rgb)

        # 提取 Alpha 通道作为 Mask
        alpha_channel = no_bg_image[:, :, 3]

        # 二值化 Mask (1=物体, 0=背景)
        _, mask = cv2.threshold(alpha_channel, 10, 255, cv2.THRESH_BINARY)

        # 找轮廓
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            print("⚠️ AI 未发现物体。")
            return None, None

        # 找最大物体
        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) < 1000:
            print("⚠️ 物体太小。")
            return None, None

        x, y, w, h = cv2.boundingRect(c)

        # ==========================================
        # 核心升级：抗透明干扰的深度计算算法
        # ==========================================

        # 1. 获取物体区域的深度数据
        obj_depth_crop = depth_image[y:y+h, x:x+w]
        obj_mask_crop = mask[y:y+h, x:x+w]

        # 2. 只保留 Mask 内的有效深度 (>0)
        valid_depths = obj_depth_crop[(obj_mask_crop > 0) & (obj_depth_crop > 0)]

        if len(valid_depths) == 0:
            print("⚠️ 深度数据丢失 (完全透明/镜面反射)")
            return None, None

        # 3. 计算“前表面”深度 (Target Distance)
        # 对于透明物体，深度图会穿透到背面。
        # 我们假设：物体上总有一些不透明的部分（瓶盖、标签、反光点）是离相机最近的。
        # 策略：取最近的 5% - 20% 的点的中位数，作为物体的前表面距离。

        sorted_depths = np.sort(valid_depths)
        p5_idx = int(len(sorted_depths) * 0.05)
        p20_idx = int(len(sorted_depths) * 0.20)

        # 取前 5% 到 20% 的数据的中位数，代表“可靠的前表面”
        if p20_idx > p5_idx:
            front_surface_depth = np.median(sorted_depths[p5_idx:p20_idx]) * self.depth_scale
        else:
            front_surface_depth = sorted_depths[0] * self.depth_scale

        print(f"🔍 锁定前表面深度: {front_surface_depth*1000:.1f} mm")

        # 4. 3D 反投影计算尺寸
        intrinsics = color_frame.profile.as_video_stream_profile().get_intrinsics()

        # 使用 AI 确定的 Bounding Box (x,y,w,h) 和 鲁棒深度 (front_surface_depth)
        p_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [x, y], front_surface_depth)
        p_br = rs.rs2_deproject_pixel_to_point(intrinsics, [x+w, y+h], front_surface_depth)

        real_w_mm = abs(p_br[0] - p_tl[0]) * 1000
        real_h_mm = abs(p_br[1] - p_tl[1]) * 1000

        # ==========================================
        # 输出
        # ==========================================

        # 生成裁切图 (保留 Alpha 通道，或者黑底)
        # 为了给 Ollama 看，黑底比较好
        clean_image = color_image.copy()
        clean_image[mask == 0] = 0 # 背景置黑

        pad = 20
        img_h, img_w = clean_image.shape[:2]
        crop_img = clean_image[
            max(0, y-pad):min(img_h, y+h+pad),
            max(0, x-pad):min(img_w, x+w+pad)
        ]

        return crop_img, (real_w_mm, real_h_mm)

    def release(self):
        self.pipeline.stop()

if __name__ == "__main__":
    scanner = AiEnhancedScanner()
    try:
        print("\n🚀 AI 扫描系统就绪! (无需设置高度阈值)")
        print("请放入物体...")
        time.sleep(1) # 模拟触发等待

        img, size = scanner.scan_object()

        if img is not None:
            width, height = size
            print(f"\n✅ 扫描完成!")
            print(f"📏 AI 测量尺寸: {width:.1f} x {height:.1f} mm")

            # 保存结果
            ts = int(time.time())
            cv2.imwrite(f"logs/ai_scan_{ts}.jpg", img)
            print(f"💾 图片已保存")

    finally:
        scanner.release()