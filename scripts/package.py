#!/usr/bin/env python3
"""Stage pristine, offline-buildable payloads from upstream's source contracts."""
import configparser
import hashlib
import io
import lzma
from pathlib import Path
import shutil
import subprocess
import tarfile
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / 'gstreamer'
STAGE = ROOT / 'target/package-src'
CACHE = ROOT / 'target/downloads'
WORK = ROOT / 'target/native-sources'
VERSION = tomllib.loads((ROOT / 'Cargo.toml').read_text())['package']['version']
NATIVE = VERSION.split('-')[0]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def fetch(url, checksum=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / hashlib.sha256(url.encode()).hexdigest()
    if not path.exists():
        with urllib.request.urlopen(url, timeout=120) as response:
            contents = response.read()
        if checksum and hashlib.sha256(contents).hexdigest() != checksum:
            raise RuntimeError(f'Checksum mismatch: {url}')
        path.write_bytes(contents)
    if checksum and hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise RuntimeError(f'Cached checksum mismatch: {url}')
    return path


def unpack(path, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path) as archive:
        archive.extractall(destination, filter='data')


def wrap(name):
    config = configparser.ConfigParser()
    config.read(WORK / 'gstreamer/subprojects' / f'{name}.wrap')
    if 'wrap-file' in config:
        spec = config['wrap-file']
        # Existing source directories bypass Meson's upstream diff_files. Keep
        # pristine inputs in its cache so Meson applies those patches in OUT_DIR.
        destination = WORK / 'gstreamer/subprojects'
        if spec.get('diff_files'):
            destination /= 'packagecache'
        path = fetch(spec['source_url'], spec['source_hash'])
        unpack(path, destination)
        if 'patch_url' in spec:
            patch = fetch(spec['patch_url'], spec['patch_hash'])
            if spec.get('diff_files'):
                shutil.copy2(patch, destination / spec['patch_filename'])
            else:
                with zipfile.ZipFile(patch) as archive:
                    archive.extractall(destination)
        return destination / spec['directory']
    spec = config['wrap-git']
    destination = WORK / 'gstreamer/subprojects' / spec.get('directory', name)
    destination.mkdir(parents=True, exist_ok=True)
    run('git', 'init', '--quiet', str(destination))
    run('git', '-C', str(destination), 'remote', 'add', 'origin', spec['url'])
    run('git', '-C', str(destination), 'fetch', '--quiet', '--depth=1', 'origin', spec['revision'])
    run('git', '-C', str(destination), 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
    if name == 'FFmpeg':
        # Upstream's release wrap names a branch; reject movement after review.
        commit = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', 'HEAD'], text=True).strip()
        assert commit == 'cde7209117ec2be9da087b9e5f96d48450fdecd2', 'review changed upstream FFmpeg branch before packaging'
    run('git', '-C', str(destination), 'submodule', 'update', '--init', '--recursive')
    return destination


SELECTED = {
    'gstreamer': None,
    'gst-plugins-base': {'gst/app', 'gst/playback', 'gst/typefind', 'gst/videoconvertscale', 'gst/videotestsrc', 'gst/volume', 'gst/audioconvert', 'gst/audioresample', 'gst/audiotestsrc', 'ext/gl'},
    'gst-plugins-good': {'gst/isomp4', 'gst/matroska', 'gst/avi', 'gst/flv', 'gst/videofilter', 'ext/jpeg'},
    'gst-plugins-bad': {'gst/videoparsers', 'gst/mpegtsdemux', 'gst/ivfparse', 'gst/codectimestamper', 'ext/aom', 'sys/applemedia', 'sys/d3d11', 'sys/nvcodec', 'sys/va'},
    'gst-plugins-ugly': {'gst/asfdemux'},
    'gst-libav': None,
}


def include_gst(path):
    parts = path.parts
    # Native shared builds install these helpers even with documentation disabled.
    if parts[:3] == ('subprojects', 'gstreamer', 'docs') and path.name in {
        'gst-hotdoc-plugins-scanner.c', 'gst-plugins-doc-cache-generator.py',
        'gst_plugins_cache.json',
    }:
        return True
    if path.name == 'gen-changelog.py' and 'scripts' in parts:
        return True
    metadata = path.name in {'meson.build', 'meson.options', 'meson_options.txt'}
    if parts[0] == 'subprojects' and len(parts) > 2:
        if parts[1] not in SELECTED and parts[1] != 'packagefiles':
            return False
        choices = SELECTED.get(parts[1])
        if choices is not None and len(parts) > 4 and parts[2] in {'gst', 'sys', 'ext'}:
            if '/'.join(parts[2:4]) not in choices and not metadata:
                return False
    if set(parts) & {'tests', 'test', 'docs', 'doc', 'examples', 'benchmarks', 'po', '.github', '.gitlab', 'release-notes'}:
        return metadata
    return True


def compress(destination, trees):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with lzma.open(destination, 'wb', preset=9) as stream:
        with tarfile.open(fileobj=stream, mode='w|') as archive:
            for source, prefix, selector in trees:
                for path in sorted(source.rglob('*')):
                    relative = path.relative_to(source)
                    if '.git' in relative.parts or (not path.is_file() and not path.is_symlink()):
                        continue
                    if not selector(relative):
                        continue
                    archive.add(path, arcname=str(Path(prefix) / relative), recursive=False)
    print(f'{destination.relative_to(ROOT)}: {destination.stat().st_size:,} bytes', flush=True)


def main():
    for helper in (ROOT / 'sources').glob('*/Cargo.toml'):
        assert tomllib.loads(helper.read_text())['package']['version'] == VERSION, helper
    run('git', '-C', str(UPSTREAM), 'diff', '--exit-code')
    run('git', '-C', str(UPSTREAM), 'diff', '--cached', '--exit-code')
    pins = subprocess.check_output(['git', '-C', str(UPSTREAM), 'submodule', 'status', '--recursive'], text=True)
    assert all(line.startswith(' ') for line in pins.splitlines()), 'initialize exact upstream submodule pins'
    declaration = subprocess.check_output(['git', '-C', str(UPSTREAM), 'show', f'{NATIVE}:meson.build'], text=True)
    assert f"version : '{NATIVE}'" in declaration
    assert subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD']) == subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', f'{NATIVE}^{{commit}}'])
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    tree = subprocess.check_output(['git', '-C', str(UPSTREAM), 'archive', NATIVE])
    with tarfile.open(fileobj=io.BytesIO(tree)) as archive:
        archive.extractall(WORK / 'gstreamer', filter='data')
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in ['Cargo.toml', 'Cargo.lock', 'build.rs', 'README.md', 'LICENSE.md', 'LICENSE-LLVM.txt']:
        shutil.copy2(ROOT / name, STAGE / name)
    shutil.copytree(ROOT / 'src', STAGE / 'src')
    for name in ['native', 'codecs', 'ffmpeg']:
        shutil.copytree(ROOT / 'sources' / name, STAGE / 'sources' / name)
        shutil.copy2(ROOT / 'LICENSE.md', STAGE / 'sources' / name / 'LICENSE.md')
    compress(STAGE / 'native.tar.xz', [(WORK / 'gstreamer', '.', include_gst)])
    support = [wrap(name) for name in ['glib', 'libffi', 'pcre2', 'zlib', 'proxy-libintl', 'orc', 'gl-headers', 'directxmath']]
    # GVDB is mandatory in the selected GLib release; preserve its nested pin.
    glib = support[0]
    cfg = configparser.ConfigParser()
    cfg.read(glib / 'subprojects/gvdb.wrap')
    spec = cfg['wrap-git']
    gvdb = glib / 'subprojects/gvdb'
    if not gvdb.exists():
        run('git', 'clone', '--quiet', spec['url'], str(gvdb))
        run('git', '-C', str(gvdb), 'checkout', '--quiet', '--detach', spec['revision'])
    cache = WORK / 'gstreamer/subprojects/packagecache'
    if cache.exists():
        support = [p for p in support if p.parent != cache] + [cache]
    compress(STAGE / 'sources/native/native.tar.xz', [(p, 'subprojects/' + p.name, lambda _: True) for p in support])
    jpeg = wrap('libjpeg-turbo')
    aom = fetch('https://storage.googleapis.com/aom-releases/libaom-3.13.3.tar.gz', '446a4ae9741cb8f3eeb98c949d25f91b48cb2b8569cae975c4b737392e9024fc')
    unpack(aom, WORK)
    compress(STAGE / 'sources/codecs/native.tar.xz', [
        (WORK / 'libaom-3.13.3', 'aom', lambda p: p.parts[0] not in {'test', 'examples', 'tools', 'doc'} or p.name == 'CMakeLists.txt' or p.suffix == '.cmake'),
        (jpeg, 'subprojects/' + jpeg.name, lambda _: True)])
    ffmpeg = wrap('FFmpeg')
    compress(STAGE / 'sources/ffmpeg/native.tar.xz', [(ffmpeg, 'subprojects/FFmpeg', lambda p: p.parts[0] not in {'doc', 'tests', '.github', '.gitlab'})])
    run('cargo', 'package', '--manifest-path', str(STAGE / 'Cargo.toml'), '--workspace', '--locked', '--no-verify')
    (ROOT / 'dist').mkdir(exist_ok=True)
    for archive in (STAGE / 'target/package').glob('*.crate'):
        size = archive.stat().st_size
        print(f'{archive.name}: {size:,} bytes', flush=True)
        assert size < 10_000_000, 'crate exceeds size limit'
        shutil.copy2(archive, ROOT / 'dist' / archive.name)


if __name__ == '__main__':
    main()
