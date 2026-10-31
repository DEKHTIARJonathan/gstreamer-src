SHELL := /bin/bash -eu -o pipefail

# Homebrew keeps these tools off PATH; Apple's Bison is too old for GStreamer.
ifeq ($(shell uname -s),Darwin)
ifneq ($(shell command -v brew 2>/dev/null),)
export PATH := $(shell brew --prefix bison)/bin:$(shell brew --prefix flex)/bin:$(PATH)
endif
endif

.PHONY: setup check-version package build test clean
setup:
	uv tool install meson==1.9.0
	uv tool install ninja
	cargo install --locked cargo-audit
	prek prepare-hooks
check-version:
	python3 -c 'import re,tomllib; from pathlib import Path; v=tomllib.loads(Path("Cargo.toml").read_text())["workspace"]["package"]["version"].split("-")[0]; assert re.search(r"version\s*:\s*"+chr(39)+re.escape(v)+chr(39),Path("gstreamer/meson.build").read_text())'
package: check-version
	python3 scripts/package.py
build: check-version
	cargo build --locked
test:
	python3 scripts/test-package.py
clean:
	rm -rf target dist testcrate/target
