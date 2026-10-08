---
name: pdf-watermark-cleaner
description: 该技能用于对用户有权修改的本地 PDF 预览、检测页首尾重复水印候选，并在明确确认区域与全页栅格化损失后清理另存。使用独立 Python CLI，无需 HTTP 服务；不用于无损编辑、绕过权限或自动删除未经确认的候选。
agent_created: true
metadata:
  version: "2.0.0"
---

# 本地 PDF 水印清理 2.0

使用 Python >=3.10、PyMuPDF 与 Pillow 独立处理本地文件。不启动 HTTP、Flask、pywebview，不下载或执行上游源码，不要求安装用户级 Skill。

## 安装与安全门槛

先检查 `scripts/bootstrap.py`、`scripts/cleaner.py`、`requirements.txt`，阅读 [参考与安装审核要求](references/api_reference.md)。运行 `python -B scripts/bootstrap.py --check` 或不带模式标志时，仅静态读取目标解释器路径存在性：不启动目标解释器、不导入第三方依赖、不创建目录、不联网安装。路径存在退出0仅表示文件存在，明确标记未验证可运行，不能视为 doctor 通过。

已有环境须先确认可信并取得用户对启动该环境的明确授权，再执行 `python -B scripts/bootstrap.py --verify --venv "可信已有隔离目录"` 运行 doctor。`--check`、`--verify`、`--install` 三者互斥；`--verify` 不安装。新环境安装后的 doctor 保留。

取得用户对**解释器、隔离目录、固定依赖及官方 PyPI 网络下载**的明确授权后，复制下列一句话命令（在技能根目录执行，按需把 python 替换为可信解释器绝对路径）：

```sh
python -B scripts/bootstrap.py --install --venv "$HOME/.cache/pdf-watermark-cleaner/venv"
```

以上为 Bash 写法；跨平台可直接省略 `--venv`，默认同为用户主目录 `.cache/pdf-watermark-cleaner/venv`。自定义路径可指向宿主管理的隔离目录。Windows PowerShell 用 `$HOME/.cache/pdf-watermark-cleaner/venv` 并以 `& "解释器路径"` 调用。安装器拒绝复用现有目录，不全局安装、不提权、不改 shell、不下载执行安装脚本；仅使用固定版本 wheel。失败时报告原始错误，不自动删除环境或绕过约束。

## 使用流程

以下 `python` 均指安装所得 venv 的 `Scripts/python.exe`（Windows）或 `bin/python`（其他系统），不是任意系统解释器。所有实际处理命令均先用 `importlib.metadata.version` 核对固定依赖版本，再正常导入；缺失或不符立即拒绝，不以先前 doctor 结果代替入口校验。

```sh
python -B scripts/cleaner.py doctor
python -B scripts/cleaner.py inspect "input.pdf"
python -B scripts/cleaner.py preview "input.pdf" 0 "page-1.png" --dpi 144
python -B scripts/cleaner.py detect "input.pdf"
```

1. 确认用户有权修改目标 PDF。原件只读，不上传；输出必须为新路径，拒绝 UNC。
2. 预览任意合法页。所有索引均为**零基**；向用户同时说明自然页码。
3. 将 detect 视为候选：仅分析前最多 12 页页首尾各 16% 的重复暗像素；可能误报页眉页脚，也可能漏检。**不得自动将候选送入 process。**
4. 按 `assets/regions.example.json` 准备配置，核对 normalized 矩形、页码、方法、DPI。`scope` 仅限制全局区域；`page_regions` 不受它限制。
5. 告知并取得明确确认：**process 栅格化所有页，包括未应用水印区域的页**；损失文字搜索/复制、矢量、链接、书签、表单及数字签名信息。若要求未选页完全无损或保留可搜索文本，停止使用此工具。
6. 用户确认实际区域和有损处理后，才传入强制确认标志：

```sh
python -B scripts/cleaner.py process "input.pdf" "confirmed-regions.json" "cleaned-new.pdf" --confirm-rasterization
```

7. 另存后核对页数、可见页尺寸、水印区域与正文；抽查选中和未选中页。没有查看图像时不得声称肉眼验收。运行 `python -B scripts/test_cleaner.py --output-dir "新建的包外测试目录"` 可复核真实合成 PDF 与像素测试。测试脚本在创建目录前拒绝解析后位于 skill 根目录及其子目录的输出路径，且拒绝复用已有输出目录。

## 边界

- 逐页生成 PNG 并写入新的 fitz 文档，不堆积所有页 PIL 图片；目标 PDF 对象仍会占用内存，不保证恒定总内存。
- `white` 是白色覆盖；`edge` 是外围像素均色填充；`blur` 只是模糊，不保证去除可辨文字。都不是无损删除水印对象。
- 不支持加密文件、不绕过修改限制、不覆盖已有文件。失败可能留下不完整的新输出，应另选路径重试，不自动删除。
- 不添加或推定上游 MIT 等许可证；仓库权限不等于第三方依赖的再分发许可。本包为本地独立实现，不内置上游源码、运行环境或测试 PDF。
- 普通 PDF 合并/文本提取不走此流程；伪造文件、掩盖来源用于欺骗等用途拒绝处理。
