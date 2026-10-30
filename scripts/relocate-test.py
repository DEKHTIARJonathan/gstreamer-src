"""Relocate a test installation, including its consumer, without SDK loader paths."""
import os
from pathlib import Path
import shutil
import subprocess


def relocate(prefix, destination, executable):
    shutil.copytree(prefix, destination, symlinks=True)
    program = destination / 'bin' / executable.name
    program.parent.mkdir(exist_ok=True)
    shutil.copy2(executable, program)
    for path in destination.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            magic = stream.read(4)
        if magic == b'\xcf\xfa\xed\xfe':
            lines = subprocess.check_output(['otool', '-L', str(path)], text=True).splitlines()[1:]
            for line in lines:
                dependency = line.strip().split(' (', 1)[0]
                dependency_path = Path(dependency).resolve()
                if dependency_path.is_relative_to(prefix.resolve()):
                    target = destination / dependency_path.relative_to(prefix.resolve())
                    relative = '@loader_path/' + os.path.relpath(target, path.parent)
                    subprocess.run(['install_name_tool', '-change', dependency, relative, str(path)], check=True, capture_output=True)
            if path.suffix == '.dylib':
                subprocess.run(['install_name_tool', '-id', '@rpath/' + path.name, str(path)], check=True, capture_output=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(path)], check=True, capture_output=True)
        elif magic == b'\x7fELF':
            relative = '$ORIGIN/' + os.path.relpath(destination / 'lib', path.parent)
            subprocess.run(['patchelf', '--set-rpath', relative, str(path)], check=True)
    return program
