#!/usr/bin/env python3
"""Build a read-only FrameForge reference in isolation against a prepared SDK."""
import argparse
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import tomllib

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference', type=Path, required=True)
parser.add_argument('--sdk', type=Path, required=True)
parser.add_argument('--hardware', action='store_true', help='require actual hardware decoding')
args = parser.parse_args()
native_version = tomllib.loads((Path(__file__).resolve().parents[1] / 'Cargo.toml').read_text())['package']['version'].split('-')[0]
work = Path(tempfile.mkdtemp(prefix='gstreamer-frameforge-'))
print(f'Isolated FrameForge check: {work}', flush=True)
# Read tracked application sources and small synthetic fixtures, never SDK caches.
names = subprocess.check_output(['git', '-C', str(args.reference), 'ls-files', '-z',
                                 'Cargo.toml', 'Cargo.lock', 'crates/playback',
                                 'crates/playback-protocol', 'tests/fixtures/playback'], text=True)
for name in names.split('\0'):
    if name:
        destination = work / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.reference / name, destination)
manifest = work / 'Cargo.toml'
text = manifest.read_text()
text = re.sub(r'(?m)^members\s*=\s*\[[\s\S]*?\]', 'members = ["crates/playback", "crates/playback-protocol"]', text, count=1)
text = re.sub(r'(?m)^default-members\s*=\s*\[[\s\S]*?\]', 'default-members = ["crates/playback"]', text, count=1)
manifest.write_text(text)
prefix = args.sdk.resolve(strict=True)
env = os.environ.copy()
for name in list(env):
    if name.startswith(('GST_', 'DYLD_', 'LD_LIBRARY_PATH', 'PKG_CONFIG', 'HOST_PKG_CONFIG', 'TARGET_PKG_CONFIG')) or name == 'LD_PRELOAD':
        env.pop(name)
env['CARGO_TARGET_DIR'] = str(work / 'target')
env['PKG_CONFIG_PATH'] = env['PKG_CONFIG_LIBDIR'] = str(prefix / 'lib/pkgconfig')
subprocess.run(['cargo', 'update', '--workspace', '--offline'], cwd=work, env=env, check=True, capture_output=True)
subprocess.run(['cargo', 'build', '--locked', '-p', 'frameforge-playback'], cwd=work, env=env, check=True)
relocate = runpy.run_path(str(Path(__file__).with_name('relocate-test.py')))['relocate']
executable = 'frameforge-playback.exe' if os.name == 'nt' else 'frameforge-playback'
runtime = work / 'runtime'
program = relocate(prefix, runtime, work / 'target/debug' / executable)
for name in list(env):
    if name.startswith('PKG_CONFIG'):
        env.pop(name)
env.update(GST_PLUGIN_SYSTEM_PATH_1_0=str(runtime / 'lib/gstreamer-1.0'),
           GST_PLUGIN_PATH_1_0='',
           GST_PLUGIN_SCANNER_1_0=str(runtime / 'libexec/gstreamer-1.0' / ('gst-plugin-scanner.exe' if os.name == 'nt' else 'gst-plugin-scanner')),
           GST_REGISTRY_1_0=str(work / 'registry.bin'))
env['PATH'] = str(runtime / 'bin') + os.pathsep + (str(Path(os.environ['SYSTEMROOT']) / 'System32') if os.name == 'nt' else '/usr/bin:/bin')
for name in ['hevc-main.mp4', 'hevc-main10.mp4', 'compat-rotation.mov']:
    fixture = work / 'tests/fixtures/playback' / name
    for attempt in range(3):
        result = subprocess.run([str(program), '--verify-runtime', str(fixture), str(fixture.with_suffix('.rgba'))],
                                env=env, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f'{name}, attempt {attempt + 1}: {result.stderr.strip()}')
        report = json.loads(result.stdout)
        assert report['version'] == 'GStreamer ' + native_version, report
        assert report['controlChecks'] >= 28 and report['savedJpegBytes'] > 0, report
        if args.hardware:
            assert report['diagnostics']['hardware'], report
        assert all(Path(plugin['path']).resolve().is_relative_to(runtime.resolve()) for plugin in report['plugins']), report
        print(json.dumps(report), flush=True)
