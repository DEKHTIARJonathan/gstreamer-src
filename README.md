# gstreamer-src

GStreamer 1.28.0 native sources and private SDK preparation, using Meson and
unmodified upstream code. This is an **unpublished, blocked release candidate**,
not yet a qualified replacement for FrameForge's runtime. The macOS native
seek regression described below fails on the required upstream baseline.

The existing `gstreamer-src` crate identity is retained. The upstream submodule
is tag `1.28.0` (`9058212f43074ef7df229e73cea135c4ea96e0d6`); Cargo's `-rc1`
suffix describes packaging, not the native ABI. `make check-version` compares
Cargo.toml with upstream's Meson version. The upstream media submodule remains
at its upstream gitlink but is not needed in the published build payload.

The complete measured source scope exceeded 20 MB compressed. With the approved
split, three dependency crates carry native support libraries, AOM/JPEG, and
FFmpeg. Each archive must remain strictly below 10,000,000 bytes. Features do
not change published source sizes. No native sources or binaries are downloaded
by consumer builds.

## Build and consumption

Prerequisites: Rust 1.98, Python 3.11+, Meson 1.9, Ninja, pkg-config, C/C++
compiler, Flex/Bison, and CMake for AOM. x86 assembly requires NASM. On macOS,
put Homebrew Bison >=2.4 before Apple's system Bison on PATH. macOS builds use
the platform compiler and deployment target 15.0. Windows AV1 requires clang-cl
with the MSVC SDK and CRT; it has not been validated on Windows. Linux graphics
features require system GL/EGL, libdrm, libva and X11 development libraries and
appropriate runtime drivers. Cross-target builds are rejected.

```sh
make setup
prek run -a
make package
make build
make test
FEATURES=app,video,static make test
FEATURES=playback,jpeg,av1,libav,orc,tools,check make test
```

`make package` stages pristine pinned sources, verifies archive checksums,
compresses native trees with XZ, then packages all four crates with Cargo.lock.
Each size is checked before testing. The build extracts and fully validates XZ
streams under OUT_DIR and installs there; it never writes into package sources.
A checkout uses `target/package-src` populated by `make package`. An extracted
registry crate uses its included payload and matching dependency packages.

Prepare the SDK in one Cargo invocation, then start consumer Cargo separately:

```sh
prefix=$(cargo run --locked --bin gstreamer-src --features playback,jpeg,av1,libav,orc,tools,check)
PKG_CONFIG_PATH="$prefix/lib/pkgconfig" PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig" \
  cargo build --manifest-path /path/to/consumer/Cargo.toml
```

Clear inherited target-specific pkg-config overrides and sysroots before this
command. Consumers keep the established gstreamer-rs bindings; their sys crates
link through the installed `.pc` files. Depending on this crate alongside
`gstreamer-sys` does **not** establish discovery or build order. Changing a build
script's environment cannot configure sibling Cargo dependencies.

`gstreamer_src::installation()` exposes the prefix, include root, library and
pkg-config directories, plugin directory, and scanner path. Shared outputs use
`lib`, `bin`, `include`, `lib/gstreamer-1.0` and `libexec/gstreamer-1.0`.
Generated GLib headers also live below `lib/glib-2.0/include`; use pkg-config's
full flags. Cargo `DEP_GSTREAMER_SRC_ROOT`/`PKGCONFIG` metadata is available to
immediate dependents but does not replace the separate preparation step.

## Compiler caching

CI installs pinned sccache in every Cargo job, uses the GitHub Actions cache
backend, and sets `RUSTC_WRAPPER=sccache` with `CARGO_INCREMENTAL=0`. The packaged
consumer inherits these settings. Native caching is explicitly enabled with
`GSTREAMER_SRC_USE_SCCACHE=1`: Meson wraps C/C++ compilers, including macOS host
and target C/C++/Objective-C/Objective-C++, and CMake uses C/C++ compiler launchers
for AOM. Missing sccache is an error when explicitly requested. Ordinary consumer
builds do not require it. Keep `CC`/`CXX` as compiler commands for CMake.

The smoke matrix uses a fresh, stable path under `target/packaged-smoke` so
changing temporary paths does not defeat reuse. Each job records compiler cache
statistics, including hits, misses and non-cacheable requests. Link steps,
compiler probes, Rust procedural macros/executables and unsupported assembler
commands may still execute directly; sccache cannot cache every build operation.

For a local disk cache, install sccache and run:

```sh
RUSTC_WRAPPER=sccache CARGO_INCREMENTAL=0 GSTREAMER_SRC_USE_SCCACHE=1 make build
sccache --show-stats
```

## Feature scope

| Feature | Native selection |
| --- | --- |
| Default: `app,video` | appsrc/appsink, video conversion/scaling, core libraries |
| `playback` | decodebin3, typefinding, MP4/MOV, Matroska/WebM, AVI, FLV, ASF, MPEG-TS, IVF, video parsers, orientation, audio helpers and synthetic sources |
| `jpeg` | libjpeg-turbo with JPEG encode/decode |
| `av1` | AOM software encode/decode, built as a PIC static dependency |
| `libav` | Private FFmpeg: H.264, HEVC, MPEG-4, H.263, MSMPEG4v2/v3, VP8/9, FLV1, WMV1/2 decoders and corresponding selected parsers |
| `gl` | OpenGL libraries, conversion and download elements |
| `applemedia` | macOS VideoToolbox plus GL; currently blocked by seek regression |
| `d3d11` | Windows D3D11 and DirectXMath |
| `va`, `nvcodec` | Linux VA; Linux/Windows NVIDIA integration, requiring platform drivers |
| `orc`, `tools`, `check` | SIMD generation, tools, GstCheck libraries |
| `static` | Core/app/video static configuration only; explicit plugin registration required |

Shared builds include core tools even without `tools`: upstream couples the
external plugin scanner to that option. Static builds do not install a scanner.
Do not use `--all-features`: static playback/codec/GPU combinations and platform
mismatches are rejected. This is a selected playback SDK, not every upstream
GStreamer plugin or every FFmpeg codec. Unselected network, DVD, GUI toolkits,
Vulkan and external GPL codec implementations are not advertised.

The support payload preserves upstream wraps: GLib 2.86.3, libffi
meson-3.2.9999.4, PCRE2 10.47, zlib 1.3.1, Orc 0.4.42, proxy-libintl, GL headers
and DirectXMath. JPEG is libjpeg-turbo 3.1.3. Archive hashes and build overlays
come from the wraps; no dependency source patches are applied. GLib's release
archive already contains its required GVDB sources. Upstream's FFmpeg wrap names
`meson-7.1.1`, a branch; packaging rejects movement from reviewed commit
`cde7209117ec2be9da087b9e5f96d48450fdecd2`. AOM 3.13.3 has no upstream monorepo
wrap: packaging explicitly uses FrameForge's source URL and SHA-256
`446a4ae9741cb8f3eeb98c949d25f91b48cb2b8569cae975c4b737392e9024fc`.

## Runtime and FrameForge migration

1. Replace Cerbero checkout/bootstrap, recipe patching, private cache markers,
   and SDK preparation with a small SDK-preparation Cargo project. Select
   `playback,jpeg,av1,libav,gl,orc,tools,check` plus the platform integrations.
2. Pass the resulting prefix to FrameForge's existing separate playback-helper
   Cargo invocation. Keep its Rust bindings and narrow playback protocol.
3. Stage libraries, plugins, scanner, encoding profiles and dependency notices
   from this installation. Rewrite platform loader paths for the application
   layout and sign the final macOS binaries. Retain Windows CRT redistribution
   and platform system/framework dependencies.
4. Keep child-only `GST_PLUGIN_SYSTEM_PATH_1_0`, empty `GST_PLUGIN_PATH_1_0`,
   explicit `GST_PLUGIN_SCANNER_1_0`, and a writable registry unique to the
   staged runtime. Invalidate registries when the runtime changes. Retain the
   helper process boundary; Linux GTK/WebKit's system GLib must remain separate.
5. Replace native prewarm inputs and cache identities in the application CI,
   then run its native seek, step, EOF, color, orientation, JPEG export and
   installer tests. Keep application resource paths, IPC, frame policy,
   entitlements, signing, installer staging and FFmpeg/MNN extraction independent.

An optional integration check copies tracked FrameForge playback sources and
small synthetic fixtures into a temporary directory. It never builds in or
changes the reference repository:

```sh
python3 scripts/test-frameforge.py --reference /path/to/FrameForge --sdk "$prefix"
# Require actual hardware selection rather than accepting software fallback:
python3 scripts/test-frameforge.py --reference /path/to/FrameForge --sdk "$prefix" --hardware
```

The wrapper's MIT license is in LICENSE.md. Payloads retain upstream licenses
and notices, including LGPL library obligations and codec licenses. Shipping a
runtime requires distributing the applicable notices and satisfying the licenses
of the components selected; static linking needs particular attention.

## Compatibility findings and release blocker

Local macOS ARM64 validation built the source checkout and extracted candidate
archives. Default and static pixel tests passed. The software playback profile
passed relocated appsrc/appsink byte/PTS checks and AV1, HEVC Main and Main10
red-frame decode checks with the original SDK hidden and developer loader paths
removed. An isolated FrameForge build used its locked gstreamer 0.25.4,
app 0.25.2 and video 0.25.4 bindings. Its software runtime passed all 28 controls
for HEVC Main, Main10 and rotated H.264, including color checks and JPEG export.
These results do not establish Linux or Windows support; that matrix is pending.

The same private installation with `applemedia` loads `vtdec_hw` successfully,
but FrameForge's original 96x64 regression fixtures reproduce incorrect seeks:
`native seek selected 800000000 instead of 600000000` (Main10), and
`native previous step selected 800000000 before 800000000` (Main).
Repeated software checks pass. Upstream `sys/applemedia/vtdec.c` sets
`is_draining` before `VTDecompressionSessionWaitForAsynchronousFrames`; the
output loop can emit frames while earlier presentation timestamps remain in
pending callbacks. FrameForge's drain-order fix remains relevant at 1.28.0.
The macOS packaged hardware smoke test also fails. Publication must stop here.

| Existing workaround | Classification for this crate |
| --- | --- |
| Cerbero Python packaging, Windows server detection/error handling, macOS build-tools target, Bison bootstrap and ZIP permissions patches | Removable with this packaging/tooling approach; Cerbero/bootstrap installers are not used |
| GLib headerless `pipe2` check on SDK 27 with deployment target 15 | Still relevant; supported Meson machine property `has_function_pipe2=false` selects upstream's existing fallback without editing source |
| AOM MSVC static/import naming and exported DATA access | Avoided by private static AOM; native Windows validation remains required |
| AV1 caps directives inside macro arguments | Still relevant to MSVC preprocessing; clang-cl is the proposed toolchain adaptation, not a claimed Windows pass |
| VideoToolbox drain ordering | Genuine remaining upstream defect, reproduced locally; no patch applied |
| Private process, scanner, registry, relocation, signing and helper fallback policy | Consumer responsibilities retained |

Smallest options are to preserve this blocked baseline, explicitly authorize the
narrow native fix, or explicitly accept software-only macOS playback. A later
upstream tag must be supplied and independently verified; it cannot be assumed
fixed. None of these choices has been silently substituted for the request.

## Publication procedure

The single workflow follows pre-commit → package/size → exact-archive consumer
matrix → trusted publishing for matching version tags → registry byte comparison.
Test artifacts are transferred as `.crate` files. `scripts/repackage.py` restores
Cargo.toml.orig, restores workspace identities in the lockfile offline, packages
with `--locked`, and requires every archive to match before publishing. Registry
verification explicitly creates its output directory and downloads exact versions.

Before any release, finish the blocked native qualification, review the full
matrix, configure trusted publishing for all dependency identities and the existing
`gstreamer-src` identity, and validate registry verification. External publication
is not authorized by local preparation. Publish helpers and root in dependency
order through Cargo's workspace publication. Do not move published RC tags.
Increment an immutable RC when source changes; retain the native 1.28.0 pin.
Stable requires new Cargo.toml/Cargo.lock versions, fresh archives and every check
again. No remote CI, publication or registry verification has been completed.
