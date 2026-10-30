use std::{
    env, fs, io,
    path::{Path, PathBuf},
    process::Command,
};
fn enabled(name: &str) -> bool {
    env::var_os(format!("CARGO_FEATURE_{}", name.to_uppercase())).is_some()
}
fn run(command: &mut Command) {
    let status = command
        .status()
        .unwrap_or_else(|e| panic!("{command:?}: {e}"));
    assert!(status.success(), "native command failed: {command:?}");
}
fn unpack(path: &Path, output: &Path) {
    let input = io::BufReader::new(fs::File::open(path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}; run make package first in a source checkout",
            path.display()
        )
    }));
    let decoder = lzma_rust2::XzReader::new(input, false);
    let mut archive = tar::Archive::new(decoder);
    archive.set_ignore_zeros(true);
    archive.unpack(output).expect("extract native sources");
    io::copy(&mut archive.into_inner(), &mut io::sink()).expect("validate complete XZ stream");
}
fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let source = out.join("sources");
    let build = out.join("build");
    let prefix = out.join("install");
    let os = env::var("CARGO_CFG_TARGET_OS").unwrap();
    for key in [
        "MESON",
        "NINJA",
        "CC",
        "CXX",
        "GSTREAMER_SRC_USE_SCCACHE",
        "SDKROOT",
        "MACOSX_DEPLOYMENT_TARGET",
    ] {
        println!("cargo:rerun-if-env-changed={key}");
    }
    let use_sccache = match env::var("GSTREAMER_SRC_USE_SCCACHE").as_deref() {
        Ok("1") => true,
        Err(_) | Ok("0") => false,
        _ => panic!("GSTREAMER_SRC_USE_SCCACHE must be 0 or 1"),
    };
    if use_sccache {
        // Meson silently drops a missing cache wrapper. Fail if explicitly requested.
        run(Command::new("sccache").arg("--version"));
    }
    println!("cargo:rerun-if-changed=native.tar.xz");
    println!("cargo:rerun-if-changed=target/package-src/native.tar.xz");
    assert!(
        env::var("HOST").unwrap() == env::var("TARGET").unwrap(),
        "cross-target builds are not yet qualified"
    );
    for (feature, allowed) in [
        ("applemedia", os == "macos"),
        ("d3d11", os == "windows"),
        ("va", os == "linux"),
        ("nvcodec", os == "linux" || os == "windows"),
    ] {
        assert!(
            !enabled(feature) || allowed,
            "unsupported platform for {feature}"
        );
    }
    if enabled("static") {
        for f in [
            "playback",
            "jpeg",
            "av1",
            "libav",
            "gl",
            "applemedia",
            "d3d11",
            "va",
            "nvcodec",
        ] {
            assert!(!enabled(f), "static configuration does not support {f}");
        }
    }
    for directory in [&source, &build, &prefix] {
        if directory.exists() {
            fs::remove_dir_all(directory).unwrap();
        }
    }
    fs::create_dir_all(&source).unwrap();
    let archive = if root.join("native.tar.xz").exists() {
        root.join("native.tar.xz")
    } else {
        root.join("target/package-src/native.tar.xz")
    };
    unpack(&archive, &source);
    unpack(&gstreamer_native_src::archive(), &source);
    #[cfg(any(feature = "jpeg", feature = "av1"))]
    unpack(&gstreamer_codecs_src::archive(), &source);
    #[cfg(feature = "libav")]
    unpack(&gstreamer_ffmpeg_src::archive(), &source);
    let c_compiler =
        env::var("CC").unwrap_or_else(|_| if os == "windows" { "clang-cl" } else { "cc" }.into());
    let cpp_compiler =
        env::var("CXX").unwrap_or_else(|_| if os == "windows" { "clang-cl" } else { "c++" }.into());
    let compiler_runtime = if os == "windows" {
        let output = Command::new("clang-cl")
            .args(["--rtlib=compiler-rt", "-print-libgcc-file-name"])
            .output()
            .expect("clang-cl must be installed with the MSVC SDK and compiler-rt");
        assert!(output.status.success(), "cannot locate compiler-rt");
        let runtime = PathBuf::from(String::from_utf8(output.stdout).unwrap().trim());
        assert!(
            runtime.is_file(),
            "missing LLVM builtins: {}",
            runtime.display()
        );
        fs::create_dir_all(prefix.join("lib")).unwrap();
        let installed = prefix.join("lib").join(runtime.file_name().unwrap());
        fs::copy(&runtime, &installed).unwrap();
        let notices = prefix.join("share/licenses/compiler-rt");
        fs::create_dir_all(&notices).unwrap();
        fs::copy(root.join("LICENSE-LLVM.txt"), notices.join("LICENSE.txt")).unwrap();
        Some(installed)
    } else {
        None
    };
    if enabled("av1") {
        run(Command::new("cmake")
            .env("CC", &c_compiler)
            .env("CXX", &cpp_compiler)
            .arg("-S")
            .arg(source.join("aom"))
            .arg("-B")
            .arg(out.join("aom-build"))
            .args([
                "-G",
                "Ninja",
                "-DBUILD_SHARED_LIBS=OFF",
                "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL",
                "-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0",
                "-DENABLE_TESTS=OFF",
                "-DENABLE_EXAMPLES=OFF",
                "-DENABLE_TOOLS=OFF",
                "-DENABLE_DOCS=OFF",
            ])
            // Set both values on every configure, including when disabling the cache.
            .arg(format!(
                "-DCMAKE_C_COMPILER_LAUNCHER={}",
                if use_sccache { "sccache" } else { "" }
            ))
            .arg(format!(
                "-DCMAKE_CXX_COMPILER_LAUNCHER={}",
                if use_sccache { "sccache" } else { "" }
            ))
            .arg(format!("-DCMAKE_INSTALL_PREFIX={}", prefix.display())));
        run(Command::new("cmake")
            .arg("--build")
            .arg(out.join("aom-build"))
            .args(["--target", "install"]));
    }
    let meson = env::var_os("MESON").unwrap_or_else(|| "meson".into());
    let mut setup = Command::new(&meson);
    setup.env("CC", &c_compiler).env("CXX", &cpp_compiler);
    setup
        .arg("setup")
        .arg(&build)
        .arg(&source)
        .arg(format!("--prefix={}", prefix.display()))
        .args([
            "--libdir=lib",
            "--buildtype=release",
            "--wrap-mode=nodownload",
            "--force-fallback-for=glib,pcre2,libffi,zlib,proxy-libintl,libjpeg-turbo,directxmath,FFmpeg",
            "-Dauto_features=disabled",
            "-Dbuild-tools-source=system",
            "-Dgst-full=disabled",
            "-Dbase=enabled",
            "-Dgood=disabled",
            "-Dbad=disabled",
            "-Dugly=disabled",
            "-Dtests=disabled",
            "-Dexamples=disabled",
            "-Dintrospection=disabled",
            "-Dglib:tests=false",
            "-Dglib:installed_tests=false",
            "-Dglib:documentation=false",
        ]);
    setup.arg(if enabled("static") {
        "-Ddefault_library=static"
    } else {
        "-Ddefault_library=shared"
    });
    if os == "windows" {
        // Reject ignored flags during capability checks: clang-cl ignores
        // /arch:SSE2 on x64, which otherwise incorrectly enables SSE4.1 code.
        setup.args([
            "-Dlibffi:default_library=static",
            "-Dc_args=-DYY_NO_UNISTD_H -Werror=unused-command-line-argument",
            "-Dcpp_args=-Werror=unused-command-line-argument",
        ]);
        // Meson invokes lld-link directly, bypassing Clang's runtime selection.
        let runtime = compiler_runtime
            .as_ref()
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/");
        for language in ["c", "cpp"] {
            setup.arg(format!(
                "-D{language}_link_args=['{}']",
                runtime.replace('\'', "\\'")
            ));
        }
    }
    let mut options = std::collections::BTreeMap::new();
    // Upstream couples the external scanner to its core tools option.
    if !enabled("static") {
        options.insert("gstreamer:tools", "enabled");
    }
    for (feature, values) in [
        ("app", vec!["gst-plugins-base:app"]),
        ("video", vec!["gst-plugins-base:videoconvertscale"]),
        (
            "playback",
            vec![
                "good",
                "bad",
                "ugly",
                "gst-plugins-base:playback",
                "gst-plugins-base:typefind",
                "gst-plugins-base:videotestsrc",
                "gst-plugins-base:audioconvert",
                "gst-plugins-base:audioresample",
                "gst-plugins-base:audiotestsrc",
                "gst-plugins-base:volume",
                "gst-plugins-good:isomp4",
                "gst-plugins-good:matroska",
                "gst-plugins-good:avi",
                "gst-plugins-good:flv",
                "gst-plugins-good:videofilter",
                "gst-plugins-bad:videoparsers",
                "gst-plugins-bad:mpegtsdemux",
                "gst-plugins-bad:ivfparse",
                "gst-plugins-bad:codectimestamper",
                "gst-plugins-ugly:asfdemux",
            ],
        ),
        ("jpeg", vec!["good", "gst-plugins-good:jpeg"]),
        ("av1", vec!["bad", "gst-plugins-bad:aom"]),
        (
            "libav",
            vec!["libav", "FFmpeg:decoders", "FFmpeg:parsers", "FFmpeg:bsfs"],
        ),
        ("gl", vec!["gst-plugins-base:gl", "gst-plugins-bad:gl"]),
        ("applemedia", vec!["bad", "gst-plugins-bad:applemedia"]),
        (
            "d3d11",
            vec!["bad", "gst-plugins-bad:d3d11", "gst-plugins-bad:d3d11-math"],
        ),
        ("va", vec!["bad", "gst-plugins-bad:va"]),
        ("nvcodec", vec!["bad", "gst-plugins-bad:nvcodec"]),
        ("orc", vec!["orc"]),
        ("tools", vec!["tools"]),
        ("check", vec!["gstreamer:check"]),
    ] {
        if enabled(feature) {
            for option in values {
                options.insert(option, "enabled");
            }
        }
    }
    if enabled("libav") {
        // FFmpeg's VP9 decoder selects this filter. With auto_features disabled,
        // omitting it silently disables the decoder even when requested.
        setup.arg("-DFFmpeg:vp9_superframe_split_bsf=enabled");
        for codec in [
            "h264",
            "hevc",
            "mpeg4",
            "h263",
            "msmpeg4v2",
            "msmpeg4v3",
            "vp8",
            "vp9",
            "flv",
            "wmv1",
            "wmv2",
        ] {
            setup.arg(format!("-DFFmpeg:{codec}_decoder=enabled"));
        }
        for codec in ["h264", "hevc", "mpeg4video", "h263", "vp8", "vp9"] {
            setup.arg(format!("-DFFmpeg:{codec}_parser=enabled"));
        }
    }
    for (option, value) in options {
        setup.arg(format!("-D{option}={value}"));
    }
    if os == "macos" {
        let machine = out.join("target.ini");
        let arch = env::var("CARGO_CFG_TARGET_ARCH").unwrap();
        let wrapper = if use_sccache { "'sccache', " } else { "" };
        let compilers = format!("[binaries]\nc = [{wrapper}'clang']\ncpp = [{wrapper}'clang++']\nobjc = [{wrapper}'clang']\nobjcpp = [{wrapper}'clang++']\n");
        if use_sccache {
            // Host tools need the wrapper as well as the target libraries.
            let native = out.join("native.ini");
            fs::write(&native, &compilers).unwrap();
            setup.arg("--native-file").arg(native);
        }
        fs::write(&machine, format!("{compilers}pkg-config = 'pkg-config'\nar = 'ar'\nstrip = 'strip'\n[host_machine]\nsystem = 'darwin'\ncpu_family = '{arch}'\ncpu = '{arch}'\nendian = 'little'\n[properties]\nneeds_exe_wrapper = false\nhas_function_pipe2 = false\n[built-in options]\nc_args = ['-mmacosx-version-min=15.0']\nc_link_args = ['-mmacosx-version-min=15.0']\ncpp_args = ['-mmacosx-version-min=15.0']\ncpp_link_args = ['-mmacosx-version-min=15.0']\nobjc_args = ['-mmacosx-version-min=15.0']\nobjc_link_args = ['-mmacosx-version-min=15.0']\nobjcpp_args = ['-mmacosx-version-min=15.0']\nobjcpp_link_args = ['-mmacosx-version-min=15.0']\n")).unwrap();
        setup.arg("--cross-file").arg(machine);
    } else if use_sccache {
        // Leave CC/CXX unwrapped for CMake; wrap only Meson's compiler commands.
        // Meson 1.9 discards its cache launcher for clang-cl. An absolute
        // executable keeps sccache in the compiler command itself.
        let wrapper = if os == "windows" {
            let path = env::split_paths(&env::var_os("PATH").unwrap())
                .map(|path| path.join("sccache.exe"))
                .find(|path| path.is_file())
                .expect("sccache.exe must be on PATH");
            format!("\"{}\"", path.display())
        } else {
            "sccache".into()
        };
        for (variable, compiler) in [("CC", c_compiler), ("CXX", cpp_compiler)] {
            let compiler = compiler.strip_prefix("sccache ").unwrap_or(&compiler);
            let command = format!("{wrapper} {compiler}");
            setup.env(variable, command);
        }
    }
    setup.env("PKG_CONFIG_PATH", prefix.join("lib/pkgconfig"));
    run(&mut setup);
    run(Command::new(&meson).arg("compile").arg("-C").arg(&build));
    run(Command::new(&meson).arg("install").arg("-C").arg(&build));
    if let Some(runtime) = compiler_runtime {
        // Export dependencies otherwise supplied implicitly by Meson's MSVC
        // linker defaults, so independent static consumers get the same link.
        let library = runtime.file_stem().unwrap().to_str().unwrap();
        for (package, libraries) in [
            ("gstreamer-1.0", format!(" -l{library}")),
            ("glib-2.0", " -luser32 -lshell32 -ladvapi32 -lole32".into()),
        ] {
            let pc = prefix.join(format!("lib/pkgconfig/{package}.pc"));
            let mut contents = fs::read_to_string(&pc).unwrap();
            if let Some(start) = contents.find("Libs.private:") {
                let end = contents[start..]
                    .find('\n')
                    .map_or(contents.len(), |i| start + i);
                contents.insert_str(end, &libraries);
            } else {
                contents.push_str(&format!("\nLibs.private:{libraries}\n"));
            }
            fs::write(pc, contents).unwrap();
        }
    }
    println!("cargo:rustc-env=GSTREAMER_SRC_PREFIX={}", prefix.display());
    println!("cargo:root={}", prefix.display());
    println!("cargo:pkgconfig={}", prefix.join("lib/pkgconfig").display());
}
