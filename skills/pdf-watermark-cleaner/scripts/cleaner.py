#!/usr/bin/env python3
"""PDF 水印清理 2.0：仅本地文件，process 将所有页栅格化。"""
import argparse
import contextlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import sys

MAX_CONFIG = 1024 * 1024
MAX_INPUT = 200 * 1024 * 1024
MAX_PIXELS = 40_000_000
PINS = {"PyMuPDF": "1.28.2", "Pillow": "12.3.0"}


def local_path(value):
    text = os.fspath(value)
    def check(s):
        if s.replace('\\', '/').startswith('//') or re.match(r'^[A-Za-z]:(?:$|[^/\\])', s):
            raise ValueError('拒绝 UNC、设备路径和盘符相对路径')
    check(text)
    path = Path(text).expanduser()
    check(str(path))
    path = path.resolve()
    check(str(path))
    return path


class PDFOutput:
    """隐藏name，避免PyMuPDF绕过已排他打开的文件句柄。"""
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        return self.stream.write(data)

    def seek(self, offset, whence=0):
        return self.stream.seek(offset, whence)

    def tell(self):
        return self.stream.tell()

    def flush(self):
        return self.stream.flush()


@contextlib.contextmanager
def new_output(value):
    path = local_path(value)
    if os.path.lexists(Path(value).expanduser()):
        raise FileExistsError('输出路径已存在（包括符号链接）')
    if not path.parent.is_dir():
        raise ValueError('输出父目录必须存在')
    # xb 是最终排他检查；不覆盖输入、硬链接或已有输出。
    with path.open('xb') as stream:
        yield stream
    # 失败时保留不完整文件，不自动删除用户可见文件。


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 存在重复键')
        result[key] = value
    return result


def load_config(value, page_count):
    path = local_path(value)
    with path.open('rb') as stream:
        raw = stream.read(MAX_CONFIG + 1)
    if len(raw) > MAX_CONFIG:
        raise ValueError('配置超过 1 MiB')
    data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object)
    return validate_config(data, page_count)


def page_index(index, count):
    if type(index) is not int or not 0 <= index < count:
        raise ValueError(f'页索引必须在 0..{count - 1} 内')
    return index


def validate_config(data, page_count):
    allowed = {'global_regions', 'page_regions', 'method', 'dpi', 'scope', 'selected_pages'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('配置必须为对象且不得含未知键')
    def regions(items):
        if not isinstance(items, list) or len(items) > 1000:
            raise ValueError('区域必须为数组，每组最多1000个')
        for item in items:
            if not isinstance(item, dict) or set(item) != {'x', 'y', 'w', 'h'}:
                raise ValueError('区域仅允许 x,y,w,h')
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in item.values()):
                raise ValueError('区域坐标必须为有限数字')
            x, y, w, h = (item[k] for k in ('x', 'y', 'w', 'h'))
            if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1-x and 0 < h <= 1-y):
                raise ValueError('区域必须在 normalized [0,1] 页面范围内')
        return items
    glob = regions(data.get('global_regions', []))
    per = data.get('page_regions', {})
    if not isinstance(per, dict):
        raise ValueError('page_regions 必须为对象')
    for key, items in per.items():
        if not isinstance(key, str) or not re.fullmatch(r'0|[1-9][0-9]*', key):
            raise ValueError('页键必须为规范零基整数字符串')
        page_index(int(key), page_count)
        regions(items)
    method = data.get('method', 'white')
    if method not in ('white', 'edge', 'blur'):
        raise ValueError('method 必须为 white/edge/blur')
    dpi = data.get('dpi', 180)
    check_dpi(dpi)
    scope = data.get('scope', 'all')
    pages = data.get('selected_pages', [])
    if scope not in ('all', 'first', 'selected') or not isinstance(pages, list):
        raise ValueError('scope/selected_pages 非法')
    for i in pages:
        page_index(i, page_count)
    if len(set(pages)) != len(pages):
        raise ValueError('selected_pages 不允许重复')
    if (scope == 'selected' and not pages) or (scope != 'selected' and pages):
        raise ValueError('仅 selected 范围允许且要求非空 selected_pages')
    if not glob and not any(per.values()):
        raise ValueError('至少需要一个区域')
    return dict(global_regions=glob, page_regions=per, method=method, dpi=dpi,
                scope=scope, selected_pages=pages)


def regions_for(config, index):
    scope = config['scope']
    applies = scope == 'all' or (scope == 'first' and index == 0) or (
        scope == 'selected' and index in config['selected_pages'])
    return (config['global_regions'] if applies else []) + config['page_regions'].get(str(index), [])


def check_dpi(dpi):
    if type(dpi) is not int or not 96 <= dpi <= 300:
        raise ValueError('DPI 必须为 96..300 的整数')


def dependencies():
    for name, required in PINS.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise ValueError(f'缺少固定依赖 {name}=={required}') from None
        if actual != required:
            raise ValueError(f'依赖版本不符：{name} 要求 {required}，实际 {actual}')
    import pymupdf as fitz
    from PIL import Image, ImageFilter, ImageStat, ImageChops
    return fitz, Image, ImageFilter, ImageStat, ImageChops


def doctor():
    versions = {}
    for name in PINS:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    ready = sys.version_info >= (3, 10) and versions == PINS
    if ready:
        dependencies()
    return {'ready': ready, 'python': sys.version.split()[0], 'executable': sys.executable,
            'dependencies': versions, 'required': PINS, 'network': False}


@contextlib.contextmanager
def open_pdf(value):
    fitz, *_ = dependencies()
    path = local_path(value)
    if not path.is_file() or path.suffix.lower() != '.pdf' or path.stat().st_size > MAX_INPUT:
        raise ValueError('输入必须为不超过200 MiB的本地PDF')
    with path.open('rb') as stream:
        if stream.read(5) != b'%PDF-':
            raise ValueError('PDF 文件头非法')
    with fitz.open(path) as doc:
        if not doc.is_pdf or doc.needs_pass or not doc.page_count:
            raise ValueError('拒绝非PDF、加密或空文档')
        if not doc.permissions & fitz.PDF_PERM_MODIFY:
            raise ValueError('文档不允许修改')
        yield doc


def render(page, dpi):
    fitz, Image, *_ = dependencies()
    check_dpi(dpi)
    rect = page.rect
    if not all(math.isfinite(v) and v > 0 for v in (rect.width, rect.height)):
        raise ValueError('页面尺寸非法')
    if math.ceil(rect.width*dpi/72) * math.ceil(rect.height*dpi/72) > MAX_PIXELS:
        raise ValueError('单页超过4000万像素上限，请降低DPI')
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def box_for(region, size):
    width, height = size
    x, y, w, h = (region[k] for k in ('x', 'y', 'w', 'h'))
    return (math.floor(x*width), math.floor(y*height),
            min(width, math.ceil((x+w)*width)), min(height, math.ceil((y+h)*height)))


def clean_image(image, regions, method):
    _, Image, ImageFilter, ImageStat, _ = dependencies()
    for region in regions:
        left, top, right, bottom = box_for(region, image.size)
        box = (left, top, right, bottom)
        if method == 'blur':
            with image.crop(box) as patch:
                with patch.filter(ImageFilter.GaussianBlur(radius=8)) as blurred:
                    image.paste(blurred, box)
        else:
            color = (255, 255, 255)
            if method == 'edge':
                strips = []
                if top > 0: strips.append((left, top-1, right, top))
                if bottom < image.height: strips.append((left, bottom, right, bottom+1))
                if left > 0: strips.append((left-1, top, left, bottom))
                if right < image.width: strips.append((right, top, right+1, bottom))
                sums, count = [0, 0, 0], 0
                for strip in strips:
                    with image.crop(strip) as patch:
                        stat = ImageStat.Stat(patch)
                        count += stat.count[0]
                        sums = [a+b for a, b in zip(sums, stat.sum)]
                if count:
                    color = tuple(round(v/count) for v in sums)
            image.paste(color, box)


def detect(doc):
    # 独立启发式：仅比较前12页归一化页首/页尾的重复暗像素。
    _, Image, _, ImageStat, ImageChops = dependencies()
    count = min(12, doc.page_count)
    masks = {}
    hits = {'header': 0, 'footer': 0}
    try:
        for index in range(count):
            with render(doc[index], 96) as image:
                for label, y0, y1 in [('header', 0, .16), ('footer', .84, 1)]:
                    with image.crop((0, int(y0*image.height), image.width, int(y1*image.height))) as band:
                        with band.resize((512, 96), Image.Resampling.BILINEAR) as small:
                            with small.convert('L') as gray:
                                mask = gray.point(lambda p: 255 if p < 215 else 0)
                    if mask.getbbox(): hits[label] += 1
                    if label not in masks:
                        masks[label] = mask
                    else:
                        merged = ImageChops.darker(masks[label], mask)
                        masks[label].close(); mask.close(); masks[label] = merged
        candidates = []
        if count >= 2:
            for label, mask in masks.items():
                bbox = mask.getbbox()
                if bbox and hits[label] == count and ImageStat.Stat(mask).sum[0]/255 >= 12:
                    l, t, r, b = bbox
                    offset = 0 if label == 'header' else .84
                    l, t, r, b = max(0,l-2), max(0,t-2), min(512,r+2), min(96,b+2)
                    candidates.append({'region': {'x': l/512, 'y': offset+t/96*.16,
                                                  'w': (r-l)/512, 'h': (b-t)/96*.16},
                                       'band': label, 'pages': list(range(count)), 'source': 'repeated-dark-pixels'})
        return {'sampled_pages': list(range(count)), 'band_fraction': .16, 'candidates': candidates,
                'warning': '仅候选；重复页眉页脚也可能命中。必须人工确认，不自动处理。'}
    finally:
        for mask in masks.values(): mask.close()


def process(doc, config, output):
    fitz, *_ = dependencies()
    changed = []
    with new_output(output) as stream, fitz.open() as target:
        for index, page in enumerate(doc):
            regions = regions_for(config, index)
            with render(page, config['dpi']) as image:
                clean_image(image, regions, config['method'])
                with io.BytesIO() as encoded:
                    image.save(encoded, format='PNG')
                    out_page = target.new_page(width=page.rect.width, height=page.rect.height)
                    out_page.insert_image(out_page.rect, stream=encoded.getvalue())
            if regions: changed.append(index)
        target.save(PDFOutput(stream), deflate=True)
    return {'path': str(local_path(output)), 'page_count': doc.page_count,
            'rasterized_pages': doc.page_count, 'region_pages': changed,
            'warning': '所有页已栅格化；文本、矢量、链接、书签、表单及签名信息丢失。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    inspect = sub.add_parser('inspect'); inspect.add_argument('input')
    preview = sub.add_parser('preview'); preview.add_argument('input'); preview.add_argument('index', type=int)
    preview.add_argument('output'); preview.add_argument('--dpi', type=int, default=144)
    detection = sub.add_parser('detect'); detection.add_argument('input')
    processing = sub.add_parser('process'); processing.add_argument('input'); processing.add_argument('config')
    processing.add_argument('output'); processing.add_argument('--confirm-rasterization', action='store_true')
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 10): raise ValueError('需要Python >=3.10')
        if args.command == 'doctor':
            result = doctor()
            print(json.dumps({'ok': result['ready'], 'result': result}, ensure_ascii=False))
            return 0 if result['ready'] else 1
        if args.command == 'process' and not args.confirm_rasterization:
            raise ValueError('必须确认区域和全页栅格化损失并传入 --confirm-rasterization')
        with open_pdf(args.input) as doc:
            if args.command == 'inspect':
                result = {'page_count': doc.page_count, 'pages': [
                    {'index': i, 'width_pt': p.rect.width, 'height_pt': p.rect.height, 'rotation': p.rotation}
                    for i, p in enumerate(doc)]}
            elif args.command == 'preview':
                page_index(args.index, doc.page_count)
                with render(doc[args.index], args.dpi) as image, new_output(args.output) as stream:
                    image.save(stream, format='PNG')
                result = {'path': str(local_path(args.output)), 'index': args.index, 'format': 'PNG'}
            elif args.command == 'detect': result = detect(doc)
            else: result = process(doc, load_config(args.config, doc.page_count), args.output)
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
