# gstreamer-src engineering rules

Keep upstream and dependency source contents unmodified. Preserve the requested
GStreamer tag and its own submodule pins. Dependency versions come from its
Meson wraps; AOM uses the documented consumer source pin.

Build only under OUT_DIR. Consumer builds must never download native inputs or
write into source directories. Fully validate compressed streams. Package from
pristine sources, preserving licenses, symlinks and executable modes.

Run prek and formatting before packaging. Enforce each compressed crate below
10,000,000 bytes before building. Test extracted archives through independent
consumers; successful compilation alone is insufficient.

Cache both Rust and supported native compiler invocations in CI, including
Meson host tools and CMake dependencies. Keep compiler cache statistics and
verify reuse with fresh build directories.

Keep existing Rust bindings and prepare the SDK before invoking consumer Cargo.
Do not assume build-script environment changes configure sibling dependencies.
Maintain private runtime process isolation, scanner and registry verification.

Never edit reference repositories. Never push, authenticate or publish without
explicit authorization. Preserve published tags and immutable versions. Compare
repacked and registry archives byte-for-byte. Stop on native compatibility
failures rather than patching upstream or silently removing functionality.

Keep versions in Cargo.toml and permanent rules here. Do not add release
controllers, source inventories or generated provenance manifests.
