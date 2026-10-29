//! Source payload used by the matching gstreamer-src release.
pub fn archive() -> std::path::PathBuf {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let archive = root.join("native.tar.xz");
    if archive.exists() {
        archive
    } else {
        root.join("../../target/package-src/sources/ffmpeg/native.tar.xz")
    }
}
