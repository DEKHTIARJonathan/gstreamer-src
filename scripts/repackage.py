#!/usr/bin/env python3
"""Reconstruct publication sources exclusively from the tested archives."""
from pathlib import Path
import shutil
import subprocess
import tarfile
import tomllib

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / 'Cargo.toml').read_text())['workspace']['package']['version']
stage = root / 'target/publication'
if stage.exists():
    shutil.rmtree(stage)
for name in ['native', 'codecs', 'ffmpeg', None]:
    package = f'gstreamer-{name}-src' if name else 'gstreamer-src'
    destination = stage / 'sources' / name if name else stage
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(root / 'dist' / f'{package}-{version}.crate') as archive:
        for member in archive:
            member.name = '/'.join(member.name.split('/')[1:])
            archive.extract(member, destination, filter='data')
    (destination / 'Cargo.toml').write_bytes((destination / 'Cargo.toml.orig').read_bytes())
    (destination / 'Cargo.toml.orig').unlink()
subprocess.run(['cargo', 'update', '--workspace', '--offline'], cwd=stage, check=True)
subprocess.run(['cargo', 'package', '--workspace', '--locked', '--no-verify', '--offline'], cwd=stage, check=True)
for candidate in (root / 'dist').glob('*.crate'):
    assert candidate.read_bytes() == (stage / 'target/package' / candidate.name).read_bytes(), candidate.name
print('Every reconstructed archive matches the tested artifact')
