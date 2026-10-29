//! Build the SDK first, then configure a separate consumer Cargo invocation.
//! This crate does not replace the gstreamer-rs bindings or configure sibling sys crates.
use std::path::{Path, PathBuf};
pub struct Installation {
    prefix: PathBuf,
}
impl Installation {
    pub fn prefix(&self) -> &Path {
        &self.prefix
    }
    pub fn include_dir(&self) -> PathBuf {
        self.prefix.join("include")
    }
    pub fn library_dir(&self) -> PathBuf {
        self.prefix.join("lib")
    }
    pub fn pkg_config_dir(&self) -> PathBuf {
        self.prefix.join("lib/pkgconfig")
    }
    pub fn plugin_dir(&self) -> PathBuf {
        self.prefix.join("lib/gstreamer-1.0")
    }
    pub fn scanner(&self) -> PathBuf {
        self.prefix
            .join("libexec/gstreamer-1.0")
            .join(if cfg!(windows) {
                "gst-plugin-scanner.exe"
            } else {
                "gst-plugin-scanner"
            })
    }
}
pub fn installation() -> Installation {
    Installation {
        prefix: env!("GSTREAMER_SRC_PREFIX").into(),
    }
}
