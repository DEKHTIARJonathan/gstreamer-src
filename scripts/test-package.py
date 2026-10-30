#!/usr/bin/env python3
"""Exercise exact extracted archives, preparing the SDK before consumer Cargo."""
import json
import runpy
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
version = tomllib.loads((ROOT / 'Cargo.toml').read_text())['package']['version']
if directory := os.environ.get('GSTREAMER_SRC_TEST_WORK_DIR'):
    # Stable paths improve cache reuse across CI jobs. Never reuse prior outputs.
    work = Path(directory).resolve()
    work.mkdir(parents=True, exist_ok=False)
else:
    work = Path(tempfile.mkdtemp(prefix='gstreamer-packaged-'))
env = os.environ.copy()
for key in list(env):
    if key.startswith(('PKG_CONFIG', 'HOST_PKG_CONFIG', 'TARGET_PKG_CONFIG', 'GST_', 'DYLD_', 'LD_LIBRARY_PATH')) or key == 'LD_PRELOAD':
        env.pop(key)
env['CARGO_TARGET_DIR'] = str(work / 'target')
for archive in (ROOT / 'dist').glob('*.crate'):
    assert archive.stat().st_size < 10_000_000
    with tarfile.open(archive) as stream:
        stream.extractall(work, filter='data')
features = os.environ.get('FEATURES', 'app,video')
prepare = work / 'prepare'
(prepare / 'src').mkdir(parents=True)
patches = '\n'.join(f'gstreamer-{name}-src = {{ path = {json.dumps(str(work / f"gstreamer-{name}-src-{version}"))} }}' for name in ['native', 'codecs', 'ffmpeg'])
(prepare / 'Cargo.toml').write_text(f'''[package]
name="prepare-gstreamer-smoke"
version="0.0.0"
edition="2021"
[dependencies]
gstreamer-src={{path={json.dumps(str(work / f'gstreamer-src-{version}'))},default-features=false,features={json.dumps(features.split(','))}}}
[patch.crates-io]
{patches}
[workspace]
''')
(prepare / 'src/main.rs').write_text('fn main() { println!("{}", gstreamer_src::installation().prefix().display()); }\n')
prefix = Path(subprocess.check_output(['cargo', 'run', '--quiet', '--manifest-path', str(prepare / 'Cargo.toml')], env=env, text=True).strip())
consumer = work / 'consumer'
shutil.copytree(ROOT / 'testcrate', consumer, ignore=shutil.ignore_patterns('target', 'Cargo.lock'))
env['PKG_CONFIG_PATH'] = env['PKG_CONFIG_LIBDIR'] = str(prefix / 'lib/pkgconfig')
env['GST_PLUGIN_SYSTEM_PATH_1_0'] = str(prefix / 'lib/gstreamer-1.0')
env['GST_PLUGIN_PATH_1_0'] = ''
env['GST_PLUGIN_SCANNER_1_0'] = str(prefix / 'libexec/gstreamer-1.0' / ('gst-plugin-scanner.exe' if os.name == 'nt' else 'gst-plugin-scanner'))
if 'static' not in features.split(','):
    assert Path(env['GST_PLUGIN_SCANNER_1_0']).is_file(), 'missing plugin scanner'
env['GST_REGISTRY_1_0'] = str(work / 'registry.bin')
env['DYLD_LIBRARY_PATH'] = env['LD_LIBRARY_PATH'] = str(prefix / 'lib')
env['PATH'] = str(prefix / 'bin') + os.pathsep + env['PATH']
env['GSTREAMER_SRC_TEST_FEATURES'] = features
env['GSTREAMER_SRC_TEST_VERSION'] = version.split('-')[0]
env['GSTREAMER_SRC_TEST_PLUGIN_DIR'] = str(prefix / 'lib/gstreamer-1.0')
if 'playback' in features.split(',') and any(f in features.split(',') for f in ['libav', 'av1']):
    fixtures = work / 'fixtures'
    fixtures.mkdir()
    cases = []
    if 'libav' in features.split(',') and 'applemedia' not in features.split(','):
        cases += [('hevc-main', 'libx265', 'yuv420p'), ('hevc-main10', 'libx265', 'yuv420p10le')]
    if 'av1' in features.split(','):
        command = [str(prefix / 'bin' / ('gst-launch-1.0.exe' if os.name == 'nt' else 'gst-launch-1.0')), '-q', 'videotestsrc', 'pattern=red', 'num-buffers=6', '!', 'video/x-raw,format=I420,width=64,height=48,framerate=6/1', '!', 'av1enc', 'cpu-used=8', '!', 'av1parse', '!', 'mp4mux', '!', 'filesink', 'location=' + str(fixtures / 'av1.mp4')]
        subprocess.run(command, env=env, check=True, timeout=120)
    for name, codec, pixels in cases:
        command = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=red:size=64x48:rate=6:duration=1', '-c:v', codec, '-pix_fmt', pixels, '-frames:v', '6']
        if codec == 'libx265':
            command += ['-x265-params', 'log-level=error:pools=1:bframes=3']
        else:
            command += ['-cpu-used', '8']
        subprocess.run(command + [str(fixtures / (name + '.mp4'))], check=True, timeout=120)
    env['GSTREAMER_SRC_TEST_FIXTURES'] = str(fixtures)
args = ['cargo', 'test', '--manifest-path', str(consumer / 'Cargo.toml')]
if 'static' in features.split(','):
    env['PKG_CONFIG_ALL_STATIC'] = '1'
    env['PKG_CONFIG_PATH'] += os.pathsep + str(prefix / 'lib/gstreamer-1.0/pkgconfig')
    args += ['--features', 'static']
result = subprocess.run(args + ['--no-run', '--message-format=json'], env=env, check=True, text=True, stdout=subprocess.PIPE, timeout=900)
executables = [Path(item['executable']) for line in result.stdout.splitlines() if (item := json.loads(line)).get('executable') and item.get('profile', {}).get('test')]
assert len(executables) == 1, executables
runtime = work / 'relocated-runtime'
relocate = runpy.run_path(str(ROOT / 'scripts/relocate-test.py'))['relocate']
program = relocate(prefix, runtime, executables[0])
assert prefix.resolve().is_relative_to(work.resolve())
prefix.rename(prefix.with_name('original-install-hidden'))
for key in list(env):
    if key.startswith(('PKG_CONFIG', 'DYLD_', 'LD_LIBRARY_PATH')):
        env.pop(key)
env['PATH'] = str(runtime / 'bin') + os.pathsep + (str(Path(os.environ['SYSTEMROOT']) / 'System32') if os.name == 'nt' else '/usr/bin:/bin')
env['GST_REGISTRY_1_0'] = str(work / 'relocated-registry.bin')
env['GST_PLUGIN_SYSTEM_PATH_1_0'] = str(runtime / 'lib/gstreamer-1.0')
env['GSTREAMER_SRC_TEST_PLUGIN_DIR'] = env['GST_PLUGIN_SYSTEM_PATH_1_0']
env['GST_PLUGIN_SCANNER_1_0'] = str(runtime / 'libexec/gstreamer-1.0' / ('gst-plugin-scanner.exe' if os.name == 'nt' else 'gst-plugin-scanner'))
try:
    subprocess.run([str(program), '--test-threads=1'], env=env, check=True, timeout=120)
finally:
    prefix.with_name('original-install-hidden').rename(prefix)
print(f'Packaged consumer passed: {work}')
