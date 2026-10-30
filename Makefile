SHELL := /bin/bash -eu -o pipefail

.PHONY: setup check-version package build test clean
setup:
	uv tool install meson==1.9.0
	uv tool install ninja
	cargo install --locked cargo-audit
	prek prepare-hooks
check-version:
	python3 -c 'import re,tomllib; from pathlib import Path; v=tomllib.loads(Path("Cargo.toml").read_text())["package"]["version"].split("-")[0]; assert re.search(r"version\s*:\s*"+chr(39)+re.escape(v)+chr(39),Path("gstreamer/meson.build").read_text())'
package: check-version
	python3 scripts/package.py
build: check-version
	cargo build --locked
test:
	python3 scripts/test-package.py
clean:
	rm -rf target dist testcrate/target
