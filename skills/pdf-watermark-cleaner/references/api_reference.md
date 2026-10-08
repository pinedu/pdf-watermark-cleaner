# 2.0 本地 CLI 参考（沿用文件名，已移除 HTTP）

## 架构与迁移

2.0.0 为独立本地实现，不执行上游源码。不再提供 base-url、timeout、upload、download、JOB、FILENAME、服务启动或 HTTP API。旧 1.0 命令不兼容；直接输入本地 PDF 和新输出路径。无需 Flask、pywebview、浏览器或后台服务。不声明发布地址，不改变上游许可证。

## 依赖与安装审核

| 项目 | 固定要求 |
|---|---|
| Python | >=3.10，可信宿主管理解释器 |
| PyMuPDF | 1.28.2 |
| Pillow | 12.3.0 |

2026-10-08 实际查询官方 PyPI 元数据，确认 `pymupdf-1.28.2-cp310-abi3-win_amd64.whl` 及 `pillow-12.3.0-cp313-cp313-win_amd64.whl` 存在。前者 SHA256 为 `ebd244918798502d7b4504c90410d1711a4d7675a32584ca30f1bab419ecbffe`，后者为 `1cca606cd25738df4ed873d5ad46bbdb3d83b5cbca291f6b4ff13a4df6b0bbe8`。这不是所有平台的兼容性承诺，也不是安全认证；安装器会在所选解释器中执行 wheel-only dry-run，再安装固定版本，不回退到源码构建。

安装前必须：
1. 审阅本地脚本和依赖清单；核对版本固定、下载源、安装目的地、无全局安装与无提权。
2. 核对依赖各自的许可证与使用条件，不推定上游或本包为 MIT，不以超管权限代替许可审阅。
3. 核对解释器来自可信宿主；核对 venv 的真实路径不是 UNC、现有环境或他人目录。
4. 获取用户对安装网络访问与环境创建的明确授权，再执行 `--install`。
5. 安装后运行 doctor 与合成样本测试；功能测试不等于供应链审计或最终认证。

```sh
python -B scripts/bootstrap.py --check --venv "隔离目录"
python -B scripts/bootstrap.py --install --venv "新的隔离目录"
```

仅使用 `--check` 或不带标志时不创建目录、不安装、不联网；环境不存在退出1并给出指引。即使环境已存在，也仅静态返回目标解释器路径存在性和 `verified:false`，明确说明未验证可运行；不启动目标解释器、不导入第三方依赖。存在时退出0不代表运行验证通过。确认已有环境可信并取得用户对启动它的明确授权后，使用 `python -B scripts/bootstrap.py --verify --venv "可信已有隔离目录"` 执行 doctor；目标不存在时失败且不安装。`--check`、`--verify` 与 `--install` 三者互斥。新建环境安装后的 doctor 保留。默认路径为 `~/.cache/pdf-watermark-cleaner/venv`，不依赖 shell 激活。所有 subprocess 都为参数数组、shell=False。安装清除 PIP_/PYTHON 环境注入并禁用 pip 配置，使用官方 PyPI、wheel-only、no-deps、no-cache，不执行下载的安装脚本；venv 使用本机 Python 自带 ensurepip。不复用已有目录，失败环境留存供检查。

本次指定解释器路径目录名为 `3.13.12`，但实际 `sys.version` 为 **3.13.14**；按指定可执行文件创建环境，报告真实版本而非目录名。跨平台版本和其他 Python 版本未在本次实际运行，不声称已经验证。

## 命令契约

```text
python -B scripts/cleaner.py doctor
python -B scripts/cleaner.py inspect INPUT
python -B scripts/cleaner.py preview INPUT INDEX OUTPUT [--dpi 144]
python -B scripts/cleaner.py detect INPUT
python -B scripts/cleaner.py process INPUT CONFIG OUTPUT --confirm-rasterization
```

- 成功 JSON：`{"ok":true,"result":...}`。运行错误 stderr JSON、退出1；参数语法错误由 argparse 退出2。
- doctor：Python 路径/版本、实际与固定依赖、ready；依赖缺少/版本不符则退出1，不安装。doctor 会正常导入符合固定版本的依赖，因此不是静态检查，只用于已授权启动的可信环境。
- inspect/preview/detect/process：入口经 open_pdf → dependencies，先用 `importlib.metadata.version` 校验 PyMuPDF/Pillow 固定版本，再正常导入；缺失或版本不符时，在导入第三方依赖和打开 PDF 前拒绝。内部处理函数也使用同一校验，不缓存旧的 doctor 结果。
- inspect：page_count、每页 index/width_pt/height_pt/rotation。尺寸是旋转后的可见 page.rect，单位 pt。
- preview：任意合法零基 INDEX，不受12页检测上限限制。输出始终为 PNG，建议 `.png`；96..300 DPI，默认144。
- detect：sampled_pages、band_fraction=.16、candidates；最多前12页。单页不构成重复证据，返回空候选。
- process：path、page_count、rasterized_pages、region_pages、损失告知。所有页栅格化，保持各页可见尺寸和朝向；旋转被烘焙入像素，不保留原始 rotation/CropBox/MediaBox 元数据。

## JSON 配置

`assets/regions.example.json` 仅示例，不代表用户确认。配置限1 MiB，使用限量读取，拒绝重复键、未知键、非法JSON、非有限数字和错误结构。

| 字段 | 默认 / 含义 |
|---|---|
| global_regions | []，受scope控制的矩形数组 |
| page_regions | {}，规范零基字符串键到矩形数组；不受scope限制 |
| method | white；可选edge、blur |
| dpi | 180；严格整数96..300，布尔值非法 |
| scope | all；可选first、selected |
| selected_pages | []；仅selected允许且必须非空；严格整数，不重复，不越界 |

矩形只含 `x,y,w,h`。以旋转后可见页面左上角为原点，横坐标除以宽、纵坐标除以高，范围 `[0,1]`；宽高必须大于0，x+w/y+h不得超过1。映射到像素时左上向下取整、右下向上取整并夹紧边界，每个正面积区域至少覆盖一个像素。不支持像素坐标或force_a4。每组最多1000个区域。

所有页索引依据实际页数校验，包括未被scope选中的page_regions键。至少提供一个区域。选中页先按 global_regions 顺序处理，再按 page_regions 顺序处理；重叠区域会被重复操作，blur可能重复模糊，必须事先说明并确认。

范围例子：scope=selected、selected_pages=[0]，global_regions仅用于自然第1页；若page_regions含键"1"，自然第2页也处理其局部区域。没有区域的页仍栅格化，但不做区域涂改。

方法：white用纯白覆盖；edge统计矩形外围相邻一像素条带均色后填充，没有外围则白色；blur仅对矩形内部做半径8像素高斯模糊。均不理解文字或修复被水印遮盖的正文。

## 候选检测实现与限制

每页96 DPI渲染，只取顶部与底部各16%，各归一化为512×96灰度图。取小于215的暗像素，求采样页间交集；至少2页、所有采样页均存在暗像素且交集至少12像素时，返回交集包围框并加2像素边距。归一化支持混合页尺寸，但位置/大小变化会漏检；正常重复页眉页脚会误报，多个对象可能被合并成较大矩形。中央水印不检测，后续页面不检测，空结果不是无水印证明。候选包含region、band、pages、source，不可直接当配置，必须人工审阅。

## I/O 与资源边界

- 不联网处理PDF；输入只读，最大200 MiB，检查文件头、格式、页数、加密与修改权限。
- 拒绝原始、用户目录展开后和路径解析后的UNC/设备路径及盘符相对路径（包括裸 `C:` 和 `C:relative.pdf`）。映射盘/特殊挂载可能由操作系统指向网络，调用方仍应提供可信本地盘。
- 输出父目录必须存在，通过`xb`排他创建，不覆盖输入、现有文件及硬链接。失败可能留下不完整新文件，不自动删除，也不原地重试覆盖。
- 单页渲染最多4000万像素；过大时明确失败。逐页关闭PIL和BytesIO，不保留所有页图片数组；fitz目标文档仍持有压缩页面资源，总内存会随文件增长。
- 不保留文本层、链接、表单、书签、附件及数字签名；不提供OCR或无损模式。
- 对恶意PDF没有额外OS沙箱保证；保持依赖更新审核，勿处理不可信超大输入以绕过上限。

## 测试与交付

`scripts/test_cleaner.py --output-dir 新的包外目录` 创建两页不同尺寸、正文与 TEST WATERMARK 页眉的PDF，真实执行inspect/preview/detect/process/doctor，并检查区域像素改善、区域外和正文像素一致、页数尺寸与原件SHA256。另测越界、缺确认、已有输出/输入保护、非法配置、UNC、scope/page_regions、13页预览/检测上限、旋转尺寸和bootstrap只读行为。还验证已有假解释器的默认/--check 不执行、不读取依赖版本、不导入依赖，显式 --verify 的调用与可信环境真实 doctor，三种模式互斥，全部处理入口的缺依赖/版本不符先于导入拒绝，以及裸盘符拒绝。测试输出路径在创建前解析，拒绝 skill 根目录及子目录（包括解析后落入其中的路径），也拒绝已有目录；默认临时目录同样检查包外边界。产物包含report.json、test-output.txt和样本。像素校验不等于肉眼检查，不等于适用于所有真实文档。

只交付工作区升级包；不要自动安装到用户Skill目录、推送、发布或做最终审计认证。不要把venv或测试PDF放入技能包；旧zip和旧审计文件不是2.0交付，未经明确要求不覆盖它们。
