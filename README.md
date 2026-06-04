# O.R.B.I.T.

**Object Recognition, Binning, and Intelligent Tracking**

O.R.B.I.T. 是 Project Entropy 中用于复杂工作室/实验室物品管理的入库原型系统。它把 RGB-D 视觉、辅助摄像头、电子秤、视觉语言模型、Homebox、标签打印机和 RFID 设备整合到一个网页控制台中，目标是把“拍照识别、人工确认、入库建档、打印标签、后续找物”做成一条可重复的半自动流程。

当前状态：入库链路已经具备可用原型，包括称重、拍照、手动框选、AI 识别、Homebox 写入、PET 标签打印和 RFID EPC 写入。自动找物、Unity/3DGS 数字孪生仍处于实验或路线图阶段。

## 项目目标

- 降低手工入库成本：物品放上称盘后，系统采集图像和重量，并生成可编辑的物品属性。
- 保留人工确认权：AI 只负责建议，正式写入 Homebox 前必须经过界面确认和编辑。
- 让标签可离线使用：PET 普通标签提供人类可读信息，二维码标签跳转到 Homebox 物品页。
- 让分类保持干净：优先复用 Homebox 中已有标签和位置，只有没有合适项时才创建新标签。
- 为后续找物和数字孪生打基础：RFID、位置树、物品照片和尺寸信息统一进入 Homebox。

## 当前能力

- 网页控制台：默认监听 `0.0.0.0:8765`，局域网设备可通过本机 IP 访问。
- D435i 俯拍视角：采集 1080p RGB 图像和深度信息，用于主体框选、尺寸估算和识别主图。
- C270 辅助视角：作为侧面辅助图像传给视觉模型，适合读取品牌、型号、标签和侧面结构。
- 手自一体框选：系统可给出初始框选，入库前可手动拖动确认；AI 识别以确认后的框选为准。
- 二次曝光/图像增强：框选后可对主体亮度进行软件重试和增强，缓解欠曝或局部过曝。
- 电子秤读数：通过 RS232/USB 串口读取稳定重量，重量不从图像中识别。
- Ollama 推理：支持本机或局域网 Ollama，界面可读取所选 Ollama 服务上的模型列表，并选择图片降采样策略。
- Homebox 集成：支持登录、读取标签/位置、创建物品、写入自定义字段、上传照片、严格复用已有标签和位置。
- TSC 标签打印：支持 TSC TTP-244 Pro 和 40mm x 20mm 标签纸，打印人类可读标签和二维码/短码标签。
- RFID 读写：可通过 E710/IE701 串口协议读取 EPC/RSSI，并在确认单标签后写入 96-bit O.R.B.I.T. 短 EPC。

## 硬件拓扑

| 模块 | 当前设备 | 用途 |
| --- | --- | --- |
| 主相机 | Intel RealSense D435i | 俯拍 RGB-D、主体框选、尺寸估算 |
| 辅助相机 | Logitech C270 | 侧面图像、辅助识别品牌和结构 |
| 电子秤 | RS232/USB 电子秤 | 稳定重量读数 |
| RFID | CP210x 串口 E710/IE701 RFID 板 | 读取 EPC/RSSI，写入 O.R.B.I.T. 短 EPC |
| 打印机 | TSC TTP-244 Pro | PET 普通标签和二维码标签 |
| 数据后台 | Homebox `http://192.168.31.3:3100` | 物品、标签、位置和照片管理 |
| AI 推理 | Ollama 本机或局域网 GPU 主机 | 视觉语言模型识别 |

## 软件结构

| 路径 | 说明 |
| --- | --- |
| `VISION/web_gui.py` | 网页控制台后端，提供 HTTP API、SSE 状态推送和硬件控制入口 |
| `VISION/web/` | 网页控制台前端 |
| `VISION/main.py` | 命令行入口，包含诊断、拍照、识别、称重等命令 |
| `VISION/vision.py` | D435i/C270 图像采集、框选、曝光重试、尺寸估算和 AI 数据规范化 |
| `VISION/scale.py` | 电子秤串口读取 |
| `VISION/homebox.py` | Homebox API 客户端，负责标签、位置、物品和附件写入 |
| `VISION/labels.py` | 标签图片生成和 TSC/TSPL 打印 |
| `VISION/rfid_e710.py` | E710/IE701 串口协议封装，支持查询读写器、盘存读取 EPC 和受控写入 EPC |
| `VISION/start_web_gui.ps1` | Windows 下启动网页控制台的脚本 |
| `VISION/add_web_gui_firewall_rule_admin.ps1` | 添加局域网访问防火墙规则 |
| `VISION/install_cp210x_driver_admin.ps1` | 安装 CP210x 串口驱动 |
| `VISION/start_ollama_vulkan.ps1` | 本机 AMD/Vulkan Ollama 实验启动脚本 |
| `VISION/logs/` | 拍照、识别、标签预览和运行日志 |

## 环境安装

项目使用 Conda 环境，依赖写在 `environment.yml` 中。

```powershell
conda env create -f environment.yml
conda activate orbit
```

如果环境已存在，可以更新：

```powershell
conda env update -f environment.yml --prune
conda activate orbit
```

## 运行配置

建议通过环境变量配置外部服务和串口。不要把 Homebox 密码提交到文档或版本库中。

```powershell
$env:HOMEBOX_URL = "http://192.168.31.3:3100"
$env:HOMEBOX_USERNAME = "<homebox account>"
$env:HOMEBOX_PASSWORD = "<homebox password>"

$env:OLLAMA_API_URL = "http://192.168.31.164:11434/api/chat"
$env:OLLAMA_MODEL = "gemma4:31b"

$env:SCALE_PORT = "COM9"
$env:RFID_PORT = "COM3"

$env:ORBIT_AUX_CAMERA_INDEX = "0"
$env:ORBIT_AUX_CAMERA_BACKEND = "dshow"
```

启动网页控制台：

```powershell
.\VISION\start_web_gui.ps1
```

也可以直接启动：

```powershell
python .\VISION\web_gui.py --host 0.0.0.0 --port 8765
```

本机访问：

```text
http://127.0.0.1:8765
```

局域网访问：

```text
http://<本机局域网IP>:8765
```

如果其他机器无法访问，先确认 Windows 防火墙是否允许端口 `8765`：

```powershell
Start-Process powershell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File .\VISION\add_web_gui_firewall_rule_admin.ps1"
```

## 入库流程

1. 将物品放在电子秤称盘上，等待重量稳定。
2. 在网页控制台点击拍照，确认 D435i 俯拍图和 C270 辅助图正常。
3. 调整绿色框选区域，使其覆盖真正要入库的物品，而不是称盘或周边杂物。
4. 点击识别待确认，系统会用确认后的框选生成识别图，并把主视角、框选上下文和辅助视角传给 Ollama。
5. 检查并编辑识别结果：名称、分类、制造商、型号、数量、重量、尺寸、标签、位置和描述。
6. 点击入库，系统只写入 Homebox，不立即打印或写 RFID。
7. 点击写标签，系统按配置打印 PET 标签，并可向天线前唯一一张 RFID 标签写入短 EPC。
8. 贴上标签后，将物品放入建议位置或手动选择的位置。

## Homebox 数据约定

- `name`：物品名称，应该是人能快速理解的名称。
- `manufacturer`：制造商或品牌，例如 `Guanglu`、`Canon`。
- `modelNumber`：真实型号或规格，例如镜头焦段、设备型号；不要把品牌写进型号。
- `tags`：作为性质分类使用，优先逐字严格复用 Homebox 已有标签。
- `suggested_location`：优先逐字严格匹配 Homebox 已有位置；匹配不到时留空供人工选择。
- `weight`：只来自电子秤，不从图像或 AI 推断。
- `measured_size`：来自框选和深度估算，仅作为辅助参考。
- `notes`：保留 AI 推理依据、入库时间、尺寸和识别上下文。

系统标签如 `General`、`AI识别`、`O.R.B.I.T.` 不应作为业务分类标签使用。

## 标签打印

每个物品计划打印两张普通 PET 标签：

- 人类可读标签：物品名、制造商/分类、重量、尺寸、标签、位置和短码。
- 二维码标签：物品名、短码、所有者联系方式和 Homebox 物品页二维码。

二维码当前只写入 Homebox 物品页面 URL，例如：

```text
http://192.168.31.3:3100/item/<item-id>
```

所有者信息可通过环境变量配置：

```powershell
$env:ORBIT_OWNER_NAME = "<name>"
$env:ORBIT_OWNER_PHONE = "<phone>"
```

RFID 当前支持读取和写入 EPC。写入前会先盘存，默认要求天线前只有一张标签；写入后会复读校验。RFID 中只写入 96-bit 短 EPC，例如 `ORB-500A0CB9`，Homebox URL 仍由二维码标签承载。

## 常用命令

诊断硬件和服务：

```powershell
python .\VISION\main.py diagnose
```

只读取电子秤：

```powershell
python .\VISION\main.py scale --port COM9
```

读取 RFID 标签 EPC：

```powershell
python .\VISION\main.py rfid-read --rfid-port COM3
```

写入 RFID 标签 EPC：

```powershell
python .\VISION\main.py rfid-write --rfid-port COM3 --epc-hex 4F52422D3530304130434239
```

拍摄 D435i 图像：

```powershell
python .\VISION\main.py capture
```

启动完整入库监听：

```powershell
python .\VISION\main.py run
```

## 已知限制

- D435i 深度分割对黑色、反光、透明和复杂背景物体不稳定；当前更依赖人工框选确认。
- RealSense 硬件 ROI 曝光在当前驱动/设备组合上可能返回 `Invalid parameter`，所以代码主要使用软件曝光重试和图像增强。
- 本机 4GB 显存运行视觉语言模型速度较慢，正式入库建议使用局域网高显存 GPU 主机上的 Ollama。
- 视觉模型可能把品牌、规格和型号混淆，入库前必须人工确认。
- RFID 写入当前只写 EPC 区，不锁卡、不写 User 区；写入时需要保证天线前只有目标标签。
- Unity/3DGS 数字孪生和 RFID RSSI 找物模式尚未接入当前网页控制台。

## 路线图

- 完成 RFID User 区写入、重复写保护和与 Homebox 记录的双向校验。
- 引入更可靠的纯视觉分割或远程 SAM 类模型，减少对深度分割的依赖。
- 增加 Homebox 位置树可视化和批量移动。
- 增加标签模板编辑器，支持不同纸张和不同标签内容密度。
- 接入 RFID 找物模式，用 RSSI 或方向天线实现近场定位提示。
- 将 Homebox 位置树、照片和 3DGS/Unity 场景关联，形成可检索的数字孪生。
