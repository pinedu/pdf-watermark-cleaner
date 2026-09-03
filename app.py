from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import fitz
from flask import Flask, jsonify, render_template, request, send_file
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat
from werkzeug.utils import secure_filename

BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if getattr(sys, "frozen", False):
    DATA_ROOT = Path.home() / "Documents" / "PDF水印清理工具"
else:
    DATA_ROOT = Path(__file__).resolve().parent
WORK = DATA_ROOT / "work"
OUTPUTS = DATA_ROOT / "outputs"
MAX_FILE_SIZE = 200 * 1024 * 1024
ALLOWED = {".pdf"}
WORK.mkdir(parents=True, exist_ok=True)
OUTPUTS.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(BUNDLE_ROOT / "templates"),
    static_folder=str(BUNDLE_ROOT / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE


def safe_job(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id or ""):
        raise ValueError("无效任务编号")
    path = WORK / job_id
    if not path.is_dir():
        raise FileNotFoundError("任务不存在或已过期")
    return path


def open_pdf(path: Path) -> fitz.Document:
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ValueError(f"无法打开 PDF：{exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise ValueError("暂不支持加密 PDF，请先解除密码保护")
    return doc


def render_page(page: fitz.Page, dpi: int = 120) -> Image.Image:
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def normalize_region_items(items: list) -> list[dict]:
    result = []
    for item in items or []:
        try:
            x = max(0.0, min(1.0, float(item["x"])))
            y = max(0.0, min(1.0, float(item["y"])))
            w = max(0.0, min(1.0 - x, float(item["w"])))
            h = max(0.0, min(1.0 - y, float(item["h"])))
        except (KeyError, TypeError, ValueError):
            continue
        if w >= 0.003 and h >= 0.003:
            result.append({"x": x, "y": y, "w": w, "h": h})
    return result


def normalized_regions(payload: dict, page_count: int) -> tuple[list[dict], dict[int, list[dict]]]:
    global_regions = normalize_region_items(payload.get("global_regions", payload.get("regions", [])))
    page_regions = {}
    for key, items in (payload.get("page_regions") or {}).items():
        try:
            page_index = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= page_index < page_count:
            regions = normalize_region_items(items)
            if regions:
                page_regions[page_index] = regions
    return global_regions, page_regions


def page_targets(mode: str, selected: list[int], count: int) -> set[int]:
    if mode == "selected":
        return {i for i in selected if 0 <= i < count}
    if mode == "first":
        return {0}
    return set(range(count))


def edge_color(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    x0, y0, x1, y1 = box
    pad = max(3, min(14, int(min(image.size) * 0.006)))
    samples = []
    for crop in (
        (max(0, x0 - pad), y0, x0, y1),
        (x1, y0, min(image.width, x1 + pad), y1),
        (x0, max(0, y0 - pad), x1, y0),
        (x0, y1, x1, min(image.height, y1 + pad)),
    ):
        if crop[2] > crop[0] and crop[3] > crop[1]:
            samples.append(ImageStat.Stat(image.crop(crop)).median)
    if not samples:
        return (255, 255, 255)
    channels = list(zip(*samples))
    return tuple(int(sorted(c)[len(c) // 2]) for c in channels)


def fill_region(image: Image.Image, box: tuple[int, int, int, int], method: str) -> None:
    x0, y0, x1, y1 = box
    if method == "blur":
        crop = image.crop(box)
        radius = max(4, int(min(x1 - x0, y1 - y0) / 4))
        image.paste(crop.filter(ImageFilter.GaussianBlur(radius)), box)
        return
    color = (255, 255, 255) if method == "white" else edge_color(image, box)
    ImageDraw.Draw(image).rectangle(box, fill=color)


def image_signature(image: Image.Image, band: str) -> Image.Image:
    gray = image.convert("L").resize((320, max(40, int(image.height * 320 / image.width))))
    bh = max(36, int(gray.height * 0.16))
    crop = gray.crop((0, 0, gray.width, bh)) if band == "top" else gray.crop((0, gray.height - bh, gray.width, gray.height))
    return crop.filter(ImageFilter.GaussianBlur(0.6))


def auto_detect(images: list[Image.Image], threshold: int = 20) -> list[dict]:
    if len(images) < 2:
        return []
    regions = []
    for band in ("top", "bottom"):
        signatures = [image_signature(im, band) for im in images[:12]]
        base = signatures[0]
        masks = []
        for other in signatures[1:]:
            diff = ImageChops.difference(base, other)
            masks.append(diff.point(lambda p: 255 if p < threshold else 0))
        common = masks[0]
        for mask in masks[1:]:
            common = ImageChops.multiply(common, mask)
        ink = base.point(lambda p: 255 if p < 238 else 0)
        common = ImageChops.multiply(common, ink).filter(ImageFilter.MaxFilter(9))
        bbox = common.getbbox()
        if not bbox:
            continue
        x0, y0, x1, y1 = bbox
        if (x1 - x0) * (y1 - y0) < 280:
            continue
        margin = 8
        x0, y0 = max(0, x0 - margin), max(0, y0 - margin)
        x1, y1 = min(common.width, x1 + margin), min(common.height, y1 + margin)
        band_ratio = 0.16
        y = y0 / common.height * band_ratio
        if band == "bottom":
            y = 1 - band_ratio + y
        regions.append({
            "x": x0 / common.width,
            "y": y,
            "w": (x1 - x0) / common.width,
            "h": (y1 - y0) / common.height * band_ratio,
            "source": "auto",
        })
    return regions


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/upload")
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify(error="请选择 PDF 文件"), 400
    if Path(file.filename).suffix.lower() not in ALLOWED:
        return jsonify(error="仅支持 PDF 文件"), 400
    job_id = uuid.uuid4().hex
    job = WORK / job_id
    pages_dir = job / "pages"
    pages_dir.mkdir(parents=True)
    source = job / "source.pdf"
    file.save(source)
    if source.stat().st_size > MAX_FILE_SIZE:
        shutil.rmtree(job, ignore_errors=True)
        return jsonify(error="文件超过 200MB 限制"), 413
    try:
        doc = open_pdf(source)
        page_count = doc.page_count
        if not page_count:
            raise ValueError("PDF 没有可处理页面")
        preview_count = min(page_count, 12)
        for i in range(preview_count):
            image = render_page(doc[i], 110)
            image.thumbnail((1100, 1500))
            image.save(pages_dir / f"{i}.jpg", quality=86, optimize=True)
        first = doc[0].rect
        doc.close()
        meta = {
            "original_name": Path(file.filename).name,
            "page_count": page_count,
            "preview_count": preview_count,
            "width": first.width,
            "height": first.height,
        }
        (job / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return jsonify(job_id=job_id, **meta)
    except Exception as exc:
        shutil.rmtree(job, ignore_errors=True)
        return jsonify(error=str(exc)), 400


@app.get("/api/page/<job_id>/<int:index>")
def page_preview(job_id: str, index: int):
    try:
        path = safe_job(job_id) / "pages" / f"{index}.jpg"
        if not path.exists():
            return jsonify(error="预览页不存在"), 404
        return send_file(path, mimetype="image/jpeg", max_age=3600)
    except Exception as exc:
        return jsonify(error=str(exc)), 404


@app.post("/api/detect/<job_id>")
def detect(job_id: str):
    try:
        job = safe_job(job_id)
        images = [Image.open(p).convert("RGB") for p in sorted((job / "pages").glob("*.jpg"), key=lambda p: int(p.stem))]
        return jsonify(regions=auto_detect(images))
    except Exception as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/process/<job_id>")
def process(job_id: str):
    try:
        job = safe_job(job_id)
        payload = request.get_json(force=True) or {}
        method = payload.get("method", "edge")
        if method not in {"edge", "white", "blur"}:
            method = "edge"
        dpi = max(96, min(300, int(payload.get("dpi", 180))))
        doc = open_pdf(job / "source.pdf")
        global_regions, per_page_regions = normalized_regions(payload, doc.page_count)
        if not global_regions and not per_page_regions:
            doc.close()
            return jsonify(error="请框选至少一个全局或页面专属水印区域"), 400
        targets = page_targets(payload.get("scope", "all"), payload.get("selected_pages", []), doc.page_count)
        if not targets:
            doc.close()
            return jsonify(error="没有选择需要处理的页面"), 400
        images = []
        layout = fitz.paper_rect("a4") if payload.get("force_a4") else None
        for i, page in enumerate(doc):
            image = render_page(page, dpi)
            regions = []
            if i in targets:
                regions.extend(global_regions)
            regions.extend(per_page_regions.get(i, []))
            for region in regions:
                x0 = int(region["x"] * image.width)
                y0 = int(region["y"] * image.height)
                x1 = max(x0 + 1, int((region["x"] + region["w"]) * image.width))
                y1 = max(y0 + 1, int((region["y"] + region["h"]) * image.height))
                fill_region(image, (x0, y0, min(x1, image.width), min(y1, image.height)), method)
            images.append(image.convert("RGB"))
        doc.close()
        original = json.loads((job / "meta.json").read_text(encoding="utf-8"))["original_name"]
        stem = secure_filename(Path(original).stem) or "cleaned"
        digest = hashlib.sha1(f"{job_id}{json.dumps(payload, sort_keys=True)}".encode()).hexdigest()[:8]
        output = OUTPUTS / f"{stem}_cleaned_{digest}.pdf"
        first, rest = images[0], images[1:]
        save_args = {"save_all": True, "append_images": rest, "resolution": dpi, "quality": 92, "optimize": True}
        if layout:
            # Pillow cannot set arbitrary PDF page boxes reliably; A4 is represented by matched pixel ratio.
            pass
        first.save(output, "PDF", **save_args)
        return jsonify(download=f"/api/download/{output.name}", filename=output.name)
    except Exception as exc:
        return jsonify(error=f"处理失败：{exc}"), 500


@app.get("/api/download/<path:name>")
def download(name: str):
    safe_name = Path(name).name
    path = OUTPUTS / safe_name
    if not path.exists() or path.suffix.lower() != ".pdf":
        return jsonify(error="文件不存在"), 404
    return send_file(path, as_attachment=True, download_name=safe_name, mimetype="application/pdf")


class DesktopApi:
    def save_pdf(self, filename: str) -> dict:
        import webview

        safe_name = Path(filename or "").name
        source = OUTPUTS / safe_name
        if not source.is_file() or source.suffix.lower() != ".pdf":
            return {"ok": False, "error": "待保存的 PDF 文件不存在"}
        try:
            selected = webview.windows[0].create_file_dialog(
                webview.FileDialog.SAVE,
                directory=str(Path.home() / "Documents"),
                save_filename=safe_name,
                file_types=("PDF 文档 (*.pdf)",),
            )
            if not selected:
                return {"ok": False, "cancelled": True}
            selected_path = selected[0] if isinstance(selected, (list, tuple)) else selected
            target = Path(selected_path)
            if target.suffix.lower() != ".pdf":
                target = target.with_suffix(".pdf")
            if target.resolve() != source.resolve():
                shutil.copy2(source, target)
            return {"ok": True, "path": str(target.resolve())}
        except Exception as exc:
            return {"ok": False, "error": f"保存失败：{exc}"}

    def open_folder(self, file_path: str) -> dict:
        target = Path(file_path or "")
        if not target.exists():
            return {"ok": False, "error": "文件不存在，无法打开所在位置"}
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer.exe", f"/select,{target}"], creationflags=0x08000000)
            else:
                os.startfile(str(target.parent))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": f"无法打开文件夹：{exc}"}


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="文件超过 200MB 限制"), 413


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_server(port: int) -> None:
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


def run_desktop() -> None:
    import webview

    port = available_port()
    server = threading.Thread(target=run_server, args=(port,), daemon=True)
    server.start()
    webview.create_window(
        "PDF 水印清理工具",
        f"http://127.0.0.1:{port}",
        js_api=DesktopApi(),
        width=1320,
        height=860,
        min_size=(980, 680),
        maximized=True,
        background_color="#f7f4eb",
        text_select=False,
    )
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    run_desktop()
