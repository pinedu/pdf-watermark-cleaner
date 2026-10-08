"""真实本地PDF端到端与负向测试；样本保存在包外的指定新目录。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import contextlib
import io

sys.dont_write_bytecode = True
import cleaner
import pymupdf as fitz
from PIL import Image, ImageChops, ImageStat

ROOT = None
METRICS = {}


def output_path(value):
    path = cleaner.local_path(value)
    skill_root = Path(__file__).resolve().parents[1]
    if path == skill_root or skill_root in path.parents:
        raise ValueError('测试输出目录不得位于 skill 根目录或其子目录')
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ROOT / 'source.pdf'
        with fitz.open() as doc:
            for w, h in [(360, 480), (420, 540)]:
                page = doc.new_page(width=w, height=h)
                page.insert_text((40, 38), 'TEST WATERMARK', fontsize=14, color=(.4, .4, .4))
                page.insert_text((40, 180), 'BODY: preserve this paragraph 123456789.', fontsize=12)
                page.draw_rect(fitz.Rect(40, 210, 260, 280), color=(0, 0, 0), fill=(.8, .9, 1))
            with cls.source.open('xb') as stream: doc.save(cleaner.PDFOutput(stream))
        cls.original_hash = digest(cls.source)
        cls.config = {'global_regions': [{'x': .05, 'y': .02, 'w': .85, 'h': .11}],
                      'page_regions': {}, 'method': 'white', 'dpi': 144,
                      'scope': 'all', 'selected_pages': []}
        cls.config_path = ROOT / 'regions.json'
        cls.config_path.write_text(json.dumps(cls.config), encoding='utf-8')

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, '-B', str(Path(cleaner.__file__)), *map(str, args)],
                                shell=False, capture_output=True, encoding='utf-8',
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return json.loads(result.stdout if ok else result.stderr)

    def test_01_real_lifecycle_pixels_hash(self):
        self.assertTrue(self.cli('doctor')['result']['ready'])
        info = self.cli('inspect', self.source)['result']
        self.assertEqual(info['page_count'], 2)
        self.cli('preview', self.source, 1, ROOT/'preview-before.png', '--dpi', 144)
        detection = self.cli('detect', self.source)['result']
        self.assertEqual(detection['sampled_pages'], [0, 1])
        self.assertTrue(detection['candidates'])
        (ROOT/'detect.json').write_text(json.dumps(detection, indent=2, ensure_ascii=False), encoding='utf-8')
        output = ROOT/'cleaned.pdf'
        result = self.cli('process', self.source, self.config_path, output, '--confirm-rasterization')['result']
        self.assertEqual(result['rasterized_pages'], 2)
        self.cli('preview', output, 1, ROOT/'preview-after.png', '--dpi', 144)
        rows = []
        with fitz.open(self.source) as before, fitz.open(output) as after:
            self.assertEqual(len(before), len(after))
            for index in range(2):
                self.assertEqual(before[index].rect, after[index].rect)
                self.assertEqual(after[index].get_text().strip(), '')
                with cleaner.render(before[index], 144) as a, cleaner.render(after[index], 144) as b:
                    box = cleaner.box_for(self.config['global_regions'][0], a.size)
                    with a.crop(box) as ac, b.crop(box) as bc:
                        dark_before = sum(255-v for v in ac.convert('L').getdata())
                        dark_after = sum(255-v for v in bc.convert('L').getdata())
                    self.assertGreater(dark_before, 0)
                    self.assertEqual(dark_after, 0)
                    with ImageChops.difference(a, b) as delta:
                        delta.paste((0, 0, 0), box)
                        self.assertIsNone(delta.getbbox(), '确认区域外像素不得变化')
                    body_box = (0, int(.25*a.height), a.width, a.height)
                    with a.crop(body_box) as ac, b.crop(body_box) as bc:
                        self.assertEqual(ac.tobytes(), bc.tobytes())
                    rows.append({'page': index, 'size_pt': list(before[index].rect),
                                 'watermark_darkness_before': dark_before, 'watermark_darkness_after': dark_after,
                                 'outside_region_changed_pixels': 0, 'body_identical': True})
        self.assertEqual(digest(self.source), self.original_hash)
        METRICS.update(pages=rows, source_sha256_before=self.original_hash,
                       source_sha256_after=digest(self.source), detect_candidates=len(detection['candidates']))

    def test_02_reject_bad_pages_confirmation_existing(self):
        for index in (-1, 2, 999):
            output = ROOT/f'invalid-{index}.png'
            self.cli('preview', self.source, index, output, ok=False)
            self.assertFalse(output.exists())
        missing = ROOT/'no-confirm.pdf'
        self.cli('process', self.source, self.config_path, missing, ok=False)
        self.assertFalse(missing.exists())
        existing = ROOT/'existing.pdf'; existing.write_bytes(b'KEEP')
        self.cli('process', self.source, self.config_path, existing, '--confirm-rasterization', ok=False)
        self.assertEqual(existing.read_bytes(), b'KEEP')
        self.cli('preview', self.source, 0, existing, ok=False)
        self.assertEqual(existing.read_bytes(), b'KEEP')
        self.cli('process', self.source, self.config_path, self.source, '--confirm-rasterization', ok=False)
        self.assertEqual(digest(self.source), self.original_hash)

    def test_03_invalid_configuration(self):
        bad = [[], {'force_a4': True}, {'dpi': True}, {'dpi': 95}, {'method': []},
               {'scope': 'selected'}, {'selected_pages': [0]}, {'page_regions': {'2': []}},
               {'page_regions': {'01': []}}, {'page_regions': {'-1': []}},
               {'scope': 'selected', 'selected_pages': [True]},
               {'scope': 'selected', 'selected_pages': [2]},
               {'scope': 'selected', 'selected_pages': [0, 0]},
               {'global_regions': [{'x': float('nan'), 'y': 0, 'w': .1, 'h': .1}]},
               {'global_regions': [{'x': .9, 'y': 0, 'w': .2, 'h': .1}]},
               {'global_regions': [{'x': True, 'y': 0, 'w': .2, 'h': .1}]},
               {'page_regions': []}]
        for index, extra in enumerate(bad):
            path = ROOT/f'bad-{index}.json'
            path.write_text(json.dumps(self.config | extra if isinstance(extra, dict) else extra), encoding='utf-8')
            self.cli('process', self.source, path, ROOT/'bad-output.pdf', '--confirm-rasterization', ok=False)
            self.assertFalse((ROOT/'bad-output.pdf').exists())
        for text in ['{"dpi":144,"dpi":180}', '{', ' '* (cleaner.MAX_CONFIG+1)]:
            path = ROOT/'bad-raw.json'; path.write_text(text, encoding='utf-8')
            with self.assertRaises((ValueError, RecursionError)): cleaner.load_config(path, 2)

    def test_04_scope_and_page_regions(self):
        config = cleaner.validate_config(self.config | {'scope': 'selected', 'selected_pages': [0],
                     'page_regions': {'1': self.config['global_regions']}}, 2)
        self.assertEqual(len(cleaner.regions_for(config, 0)), 1)
        self.assertEqual(len(cleaner.regions_for(config, 1)), 1)
        first = cleaner.validate_config(self.config | {'scope': 'first'}, 2)
        self.assertEqual(cleaner.regions_for(first, 1), [])
        per_path = ROOT/'per-page.json'
        per_path.write_text(json.dumps(config), encoding='utf-8')
        per_result = self.cli('process', self.source, per_path, ROOT/'per-page.pdf', '--confirm-rasterization')['result']
        self.assertEqual(per_result['region_pages'], [0, 1])
        path = ROOT/'first.json'; path.write_text(json.dumps(first), encoding='utf-8')
        self.cli('process', self.source, path, ROOT/'first-only.pdf', '--confirm-rasterization')
        with fitz.open(self.source) as a, fitz.open(ROOT/'first-only.pdf') as b:
            with cleaner.render(a[1],144) as x, cleaner.render(b[1],144) as y:
                self.assertEqual(x.tobytes(), y.tobytes())
            self.assertEqual(b[1].get_text(), '')

    def test_05_preview_beyond_12_detect_limit_rotation(self):
        path = ROOT/'thirteen.pdf'
        with fitz.open() as doc:
            for i in range(13):
                p = doc.new_page(width=240, height=320)
                p.insert_text((20, 25), 'REPEATED HEADER')
            doc[12].set_rotation(90)
            with path.open('xb') as stream: doc.save(cleaner.PDFOutput(stream))
        self.cli('preview', path, 12, ROOT/'page-13.png')
        result = self.cli('detect', path)['result']
        self.assertEqual(result['sampled_pages'], list(range(12)))
        self.cli('process', path, self.config_path, ROOT/'rotated.pdf', '--confirm-rasterization')
        with fitz.open(path) as a, fitz.open(ROOT/'rotated.pdf') as b:
            self.assertEqual(a[12].rect, b[12].rect)

    def test_06_local_paths_methods_and_limits(self):
        for value in ['//server/share/file.pdf', r'\\server\share\file.pdf', r'\\?\C:\file.pdf', 'C:relative.pdf', 'C:', 'z:']:
            with self.assertRaises(ValueError): cleaner.local_path(value)
        for method in ('white', 'edge', 'blur'):
            with Image.new('RGB', (100, 100), 'white') as image:
                image.paste((0,0,0), (25,25,75,75))
                cleaner.clean_image(image, [{'x':.2,'y':.2,'w':.6,'h':.6}], method)
                self.assertEqual(image.getpixel((0,0)), (255,255,255))
        with fitz.open() as doc:
            page = doc.new_page(width=20000, height=20000)
            with self.assertRaises(ValueError): cleaner.render(page, 300)

    def test_07_encrypted_and_non_pdf(self):
        path = ROOT/'encrypted.pdf'
        with fitz.open(self.source) as doc, path.open('xb') as stream:
            doc.save(cleaner.PDFOutput(stream), encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='test')
        self.cli('inspect', path, ok=False)
        fake = ROOT/'fake.pdf'; fake.write_bytes(b'not a pdf')
        self.cli('inspect', fake, ok=False)
        single = ROOT/'single.pdf'
        with fitz.open(self.source) as doc, fitz.open() as target, single.open('xb') as stream:
            target.insert_pdf(doc, from_page=0, to_page=0)
            target.save(cleaner.PDFOutput(stream))
        self.assertEqual(self.cli('detect', single)['result']['candidates'], [])

    def test_08_bootstrap_readonly(self):
        target = ROOT/'must-not-exist'
        script = Path(cleaner.__file__).with_name('bootstrap.py')
        for flags in ([], ['--check']):
            result = subprocess.run([sys.executable, '-B', str(script), '--venv', str(target), *flags],
                                    shell=False, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(target.exists())
        result = subprocess.run([sys.executable, '-B', str(script), '--check', '--install'],
                                shell=False, capture_output=True)
        self.assertEqual(result.returncode, 2)
        import bootstrap
        with mock.patch.object(bootstrap, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bootstrap.main(['--install', '--venv', str(ROOT)]), 1)
            run.assert_not_called()
        with mock.patch.object(bootstrap.sys, 'version_info', (3, 9)), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bootstrap.main(['--check', '--venv', str(target)]), 1)
        self.assertFalse(target.exists())

    def test_09_existing_bootstrap_static_and_verify(self):
        import bootstrap
        target = ROOT/'fake-venv'
        exe = bootstrap.python_in(target)
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'NOT AN EXECUTABLE')
        for flags in ([], ['--check']):
            output = io.StringIO()
            with mock.patch.object(bootstrap, 'run') as run, \
                    mock.patch.object(cleaner, 'dependencies') as deps, \
                    mock.patch.object(cleaner.importlib.metadata, 'version') as version, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(bootstrap.main(['--venv', str(target), *flags]), 0)
                run.assert_not_called(); deps.assert_not_called(); version.assert_not_called()
            data = json.loads(output.getvalue())
            self.assertTrue(data['exists'])
            self.assertFalse(data['verified'])
            self.assertIn('未验证可运行', data['guidance'])
        with mock.patch.object(bootstrap, 'run') as run:
            self.assertEqual(bootstrap.main(['--verify', '--venv', str(target)]), 0)
            run.assert_called_once_with([exe, '-I', '-B', Path(cleaner.__file__).with_name('cleaner.py'), 'doctor'])
        with mock.patch.object(bootstrap, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bootstrap.main(['--verify', '--venv', str(ROOT/'missing-verify')]), 1)
            run.assert_not_called()
        for flags in (['--check', '--verify'], ['--install', '--verify'], ['--check', '--install']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                bootstrap.main(flags)
            self.assertEqual(error.exception.code, 2)
        result = subprocess.run([sys.executable, '-B', str(Path(bootstrap.__file__)),
                                 '--verify', '--venv', sys.prefix], shell=False,
                                capture_output=True, encoding='utf-8',
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['result']['ready'])

    def test_10_fixed_versions_before_import_for_all_commands(self):
        commands = [('inspect', self.source), ('preview', self.source, '0', ROOT/'version.png'),
                    ('detect', self.source), ('process', self.source, self.config_path,
                                             ROOT/'version.pdf', '--confirm-rasterization')]
        import builtins
        original_import = builtins.__import__
        for package in cleaner.PINS:
            for missing in (False, True):
                def version(name):
                    if name == package:
                        if missing:
                            raise cleaner.importlib.metadata.PackageNotFoundError(name)
                        return '0.0.0'
                    return cleaner.PINS[name]
                def guarded_import(name, *args, **kwargs):
                    if name.split('.')[0] in ('pymupdf', 'PIL'):
                        self.fail('版本校验失败时不得导入第三方依赖')
                    return original_import(name, *args, **kwargs)
                for command in commands:
                    with self.subTest(package=package, missing=missing, command=command[0]):
                        error = io.StringIO()
                        with mock.patch.object(cleaner.importlib.metadata, 'version', side_effect=version), \
                                mock.patch.object(builtins, '__import__', side_effect=guarded_import), \
                                contextlib.redirect_stderr(error):
                            self.assertEqual(cleaner.main(list(map(str, command))), 1)
                        self.assertIn(package, json.loads(error.getvalue())['error'])
        self.assertFalse((ROOT/'version.png').exists())
        self.assertFalse((ROOT/'version.pdf').exists())

    def test_11_output_directory_outside_skill(self):
        skill_root = Path(__file__).resolve().parents[1]
        for path in (skill_root, skill_root/'forbidden-test-output',
                     skill_root/'scripts'/'..'/'forbidden-test-output'):
            with self.assertRaises(ValueError): output_path(path)
        self.assertEqual(output_path(ROOT/'allowed'), ROOT/'allowed')
        rejected = skill_root/'forbidden-test-output'
        result = subprocess.run([sys.executable, '-B', str(Path(__file__)), '--output-dir', str(rejected)],
                                shell=False, capture_output=True, encoding='utf-8',
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('测试输出目录不得位于', result.stderr)
        self.assertFalse(rejected.exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir')
    args = parser.parse_args()
    if args.output_dir:
        ROOT = output_path(args.output_dir)
        ROOT.mkdir(exist_ok=False)
    else:
        ROOT = Path(tempfile.mkdtemp(prefix='pdf-cleaner-v2-tests-', dir=output_path(tempfile.gettempdir())))
    with (ROOT/'test-output.txt').open('x', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    print((ROOT/'test-output.txt').read_text(encoding='utf-8'))
    report = {'successful': result.wasSuccessful(), 'tests_run': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors),
              'doctor': cleaner.doctor(), 'metrics': METRICS,
              'visual_review': False, 'output_dir': str(ROOT)}
    (ROOT/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(0 if result.wasSuccessful() else 1)
