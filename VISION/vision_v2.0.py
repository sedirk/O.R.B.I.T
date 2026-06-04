import pyrealsense2 as rs
import numpy as np
import cv2
import time
import base64
import requests
import json
from rembg import remove
# ================= 配置区域 =================
# 1. 确保这里写的名字和你 ollama list 里的一模一样！
#    如果是 qwen3-vl:latest，就写 qwen3-vl
OLLAMA_MODEL = "qwen3-vl:8b"

# 2. 强制使用 Chat 接口 (视觉模型的标准)
OLLAMA_API_URL = "http://192.168.31.164:11434/api/chat"
# ===========================================

class IntelligentScanner:
    def __init__(self):
        print("⚡ 初始化系统: RealSense + Rembg + Ollama...")

        # 1. RealSense 配置 (保持最高画质)
        self.pipeline = rs.pipeline()
        self.config = rs.config()
        self.config.enable_stream(rs.stream.depth, 1280, 720, rs.format.z16, 30)
        self.config.enable_stream(rs.stream.color, 1920, 1080, rs.format.bgr8, 30)

        self.align = rs.align(rs.stream.color)

        try:
            self.profile = self.pipeline.start(self.config)
            print("✅ 相机启动成功")
        except Exception as e:
            print(f"❌ 相机启动失败: {e}")
            exit()

        self.depth_scale = self.profile.get_device().first_depth_sensor().get_depth_scale()

        # 滤波器
        self.spatial = rs.spatial_filter()
        self.spatial.set_option(rs.option.holes_fill, 3)
        self.temporal = rs.temporal_filter()

    def image_to_base64(self, image):
        """将图像转换为 Base64，同时压缩分辨率以防 Token 溢出"""
        # 1. 缩放: Qwen-VL 不需要 4K 分辨率，1024x1024 足够了，太大容易导致模型“死机”或超时
        h, w = image.shape[:2]
        max_size = 1024
        if max(h, w) > max_size:
            scale = max_size / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            image = cv2.resize(image, (new_w, new_h))

        # 2. 转 RGB (去除 Alpha 通道)
        if len(image.shape) == 3 and image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        # 3. 编码
        _, buffer = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        return base64.b64encode(buffer).decode('utf-8')

    def ask_ollama(self, image, width_mm, height_mm):
        """使用标准的 Chat 接口发送请求"""
        print(f"🚀 正在发送给 {OLLAMA_MODEL} (Chat Mode)...")

        base64_img = self.image_to_base64(image)

        # 提示词
        prompt = f"""
        你是一个智能仓储助手。
        【传感器数据（仅供参考，可能存在误差）】：测得物理宽度约 {width_mm:.1f}mm，高度约 {height_mm:.1f}mm。

        请识别图中的物体。
        注意：
        1. 如果视觉特征非常明显（例如明显的步进电机接口、NEMA 17外形），请优先相信视觉判断，忽略可能的尺寸测量误差。
        2. 如果物体是黑色的，传感器深度往往不准，导致尺寸偏大，请考虑到这一点。
        必须直接输出 JSON 格式，不要包含 Markdown 标记。格式如下：
        {{
            "name": "物体名称",
            "category": "分类建议",
            "description": "简短描述",
            "reasoning": "结合尺寸的推理逻辑"
        }}
        """

        # 标准 Chat Payload
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64_img]
                }
            ],
            "stream": False,
            "format": "json", # 强制 JSON 输出
            "options": {
                "temperature": 0.1, # 低温度，更精准
                "num_ctx": 4096     # 保证视觉上下文够用
            }
        }

        try:
            print("⏳ 等待 AI 响应 (Chat)...")
            start_t = time.time()

            # 设置 120秒超时，因为视觉模型有时候很慢
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=120)

            print(f"⏱️ 网络耗时: {time.time() - start_t:.2f}s")

            if response.status_code != 200:
                print(f"❌ API 报错: {response.status_code}")
                # 打印出错的原始信息，帮你找原因
                print(f"❌ 错误详情: {response.text}")
                return None

            result = response.json()

            # 调试：打印一下 keys 看看结构对不对
            # print(f"Debug Keys: {result.keys()}")

            # 标准 Chat 接口返回结构
            if 'message' in result:
                content = result['message']['content']
                return content
            elif 'response' in result:
                # 防御性编程：万一你没改 URL，这里也能兜底
                return result['response']
            else:
                print(f"⚠️ 响应结构极其异常: {result}")
                return None

        except Exception as e:
            print(f"❌ 连接/处理错误: {e}")
            return None

    def process_frame(self, color_frame, depth_frame):
        """处理单帧：抠图 + 测量"""
        # 预处理深度
        filtered_depth = self.spatial.process(depth_frame)
        filtered_depth = self.temporal.process(filtered_depth)
        depth_frame = filtered_depth.as_depth_frame()

        color_image = np.asanyarray(color_frame.get_data())
        depth_image = np.asanyarray(depth_frame.get_data())

        # 1. AI 抠图 (Rembg)
        color_rgb = cv2.cvtColor(color_image, cv2.COLOR_BGR2RGB)
        no_bg_image = remove(color_rgb)
        alpha_channel = no_bg_image[:, :, 3]
        _, mask = cv2.threshold(alpha_channel, 10, 255, cv2.THRESH_BINARY)

        # 找轮廓
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours: return None, None, None

        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) < 1000: return None, None, None

        x, y, w, h = cv2.boundingRect(c)

        # 2. 鲁棒深度测量
        obj_depth_crop = depth_image[y:y+h, x:x+w]
        obj_mask_crop = mask[y:y+h, x:x+w]
        valid_depths = obj_depth_crop[(obj_mask_crop > 0) & (obj_depth_crop > 0)]

        if len(valid_depths) == 0: return None, None, None

        # 取前 20% 最近点 (抗透明穿透)
        sorted_depths = np.sort(valid_depths)
        p_idx = int(len(sorted_depths) * 0.20)
        front_depth = sorted_depths[p_idx] * self.depth_scale

        # 3. 反投影算尺寸
        intrinsics = color_frame.profile.as_video_stream_profile().get_intrinsics()
        p_tl = rs.rs2_deproject_pixel_to_point(intrinsics, [x, y], front_depth)
        p_br = rs.rs2_deproject_pixel_to_point(intrinsics, [x+w, y+h], front_depth)

        real_w = abs(p_br[0] - p_tl[0]) * 1000
        real_h = abs(p_br[1] - p_tl[1]) * 1000

        # 4. 裁切图片
        clean_image = color_image.copy()
        clean_image[mask == 0] = 0 # 黑底
        pad = 20
        crop = clean_image[max(0, y-pad):min(1080, y+h+pad), max(0, x-pad):min(1920, x+w+pad)]

        return crop, real_w, real_h

    def run(self):
        print("\n=== 📷 智能扫描仪已启动 ===")
        print(" [SPACE] 空格键: 触发识别")
        print(" [Q]     退出程序")

        cv2.namedWindow("Viewfinder", cv2.WINDOW_NORMAL)

        while True:
            # 获取帧
            frames = self.pipeline.wait_for_frames()
            aligned_frames = self.align.process(frames)
            color_frame = aligned_frames.get_color_frame()
            depth_frame = aligned_frames.get_depth_frame()

            if not color_frame or not depth_frame: continue

            # 实时预览流 (转为 numpy)
            curr_image = np.asanyarray(color_frame.get_data())

            # 在预览上画一个框，提示用户把东西放中间
            h, w = curr_image.shape[:2]
            cv2.rectangle(curr_image, (w//4, h//4), (w*3//4, h*3//4), (0, 255, 0), 2)
            cv2.putText(curr_image, "Place Object Here", (w//4, h//4 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

            # 为了适应 GPD 屏幕，缩放预览图
            preview_img = cv2.resize(curr_image, (1280, 720))
            cv2.imshow("Viewfinder", preview_img)

            key = cv2.waitKey(1) & 0xFF

            # === 触发逻辑 ===
            if key == 32: # SPACE
                print("\n📸 正在捕获并处理...")

                # 在画面上显示 "Processing..."
                cv2.putText(preview_img, "Processing...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
                cv2.imshow("Viewfinder", preview_img)
                cv2.waitKey(1) # 强制刷新界面

                # 开始处理 (耗时操作)
                start_time = time.time()
                crop_img, width, height = self.process_frame(color_frame, depth_frame)

                if crop_img is not None:
                    print(f"✅ 测量完成: {width:.1f}x{height:.1f}mm")
                    print(f"⏳ 正在呼叫 Qwen3-VL...")

                    # 调用 AI
                    ai_response = self.ask_ollama(crop_img, width, height)

                    print("-" * 40)
                    try:
                        # 尝试解析并漂亮地打印 JSON
                        data = json.loads(ai_response)
                        print(json.dumps(data, indent=4, ensure_ascii=False))
                    except:
                        # 如果 AI 没返回 JSON，直接打印原始文本
                        print(ai_response)
                    print("-" * 40)
                    print(f"耗时: {time.time() - start_time:.2f}s")

                    # 显示裁切图结果
                    cv2.imshow("Result Crop", crop_img)

                else:
                    print("⚠️ 未检测到物体，请调整位置")

            elif key == ord('q'):
                break

        self.pipeline.stop()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    app = IntelligentScanner()
    app.run()