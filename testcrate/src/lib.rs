#[test]
fn native_version_and_pixel_round_trip() {
    use gstreamer::{self as gst, prelude::*};
    gst::init().unwrap();
    #[cfg(feature = "static")]
    unsafe {
        extern "C" {
            fn gst_plugin_coreelements_register();
            fn gst_plugin_app_register();
            fn gst_plugin_videoconvertscale_register();
        }
        gst_plugin_coreelements_register();
        gst_plugin_app_register();
        gst_plugin_videoconvertscale_register();
    }
    let (major, minor, micro, _) = gst::version();
    let expected: Vec<u32> = std::env::var("GSTREAMER_SRC_TEST_VERSION")
        .expect("runner must provide the native version")
        .split('.')
        .map(|part| part.parse().unwrap())
        .collect();
    assert_eq!(vec![major, minor, micro], expected);
    let pipeline = gst::parse::launch("appsrc name=source format=time ! videoconvert ! video/x-raw,format=RGBA ! appsink name=sink sync=false")
        .unwrap().downcast::<gst::Pipeline>().unwrap();
    let source = pipeline
        .by_name("source")
        .unwrap()
        .downcast::<gstreamer_app::AppSrc>()
        .unwrap();
    let sink = pipeline
        .by_name("sink")
        .unwrap()
        .downcast::<gstreamer_app::AppSink>()
        .unwrap();
    let info = gstreamer_video::VideoInfo::builder(gstreamer_video::VideoFormat::Rgba, 2, 2)
        .fps(gst::Fraction::new(1, 1))
        .build()
        .unwrap();
    source.set_caps(Some(&info.to_caps().unwrap()));
    let pixels = vec![
        255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255,
    ];
    let mut buffer = gst::Buffer::from_mut_slice(pixels.clone());
    buffer.get_mut().unwrap().set_pts(gst::ClockTime::ZERO);
    buffer
        .get_mut()
        .unwrap()
        .set_duration(gst::ClockTime::SECOND);
    pipeline.set_state(gst::State::Playing).unwrap();
    source.push_buffer(buffer).unwrap();
    source.end_of_stream().unwrap();
    let sample = sink
        .try_pull_sample(gst::ClockTime::from_seconds(10))
        .expect("timed out waiting for pixels");
    let output = sample.buffer().unwrap();
    assert_eq!(output.pts(), Some(gst::ClockTime::ZERO));
    assert_eq!(output.map_readable().unwrap().as_slice(), pixels);
    let bus = pipeline.bus().unwrap();
    let message = bus
        .timed_pop_filtered(
            gst::ClockTime::from_seconds(10),
            &[gst::MessageType::Eos, gst::MessageType::Error],
        )
        .unwrap();
    assert!(
        matches!(message.view(), gst::MessageView::Eos(_)),
        "{message:?}"
    );
    pipeline.set_state(gst::State::Null).unwrap();
}

#[test]
fn selected_plugins_and_codecs() {
    use gstreamer::{self as gst, prelude::*};
    let features = std::env::var("GSTREAMER_SRC_TEST_FEATURES").unwrap_or_default();
    if features.split(',').any(|f| f == "static") {
        return; // Registration is exercised by the static pixel test.
    }
    gst::init().unwrap();
    let has = |f| features.split(',').any(|value| value == f);
    let root = std::path::PathBuf::from(std::env::var_os("GSTREAMER_SRC_TEST_PLUGIN_DIR").unwrap());
    let mut names = vec!["appsrc", "appsink", "videoconvert"];
    if has("playback") {
        names.extend([
            "decodebin3",
            "qtdemux",
            "matroskademux",
            "h265parse",
            "asfdemux",
            "videoflip",
        ]);
    }
    if has("jpeg") {
        names.extend(["jpegenc", "jpegdec"]);
    }
    if has("libav") {
        names.push("avdec_h265");
    }
    if has("av1") {
        names.push("av1dec");
    }
    for name in names {
        let factory = gst::ElementFactory::find(name).unwrap_or_else(|| panic!("missing {name}"));
        let plugin = factory.plugin().unwrap();
        let path = plugin.filename().unwrap();
        assert!(
            path.starts_with(&root),
            "{name} loaded from {}",
            path.display()
        );
    }
    if let Some(directory) = std::env::var_os("GSTREAMER_SRC_TEST_FIXTURES") {
        for file in std::fs::read_dir(directory).unwrap() {
            let file = file.unwrap().path();
            let pipeline = gst::Pipeline::new();
            let input = gst::ElementFactory::make("filesrc")
                .property("location", file.to_str().unwrap())
                .build()
                .unwrap();
            let decoder = gst::ElementFactory::make("decodebin3").build().unwrap();
            let convert = gst::ElementFactory::make("videoconvert").build().unwrap();
            let sink = gstreamer_app::AppSink::builder()
                .sync(false)
                .wait_on_eos(false)
                .caps(
                    &gst::Caps::builder("video/x-raw")
                        .field("format", "RGBA")
                        .build(),
                )
                .build();
            pipeline
                .add_many([&input, &decoder, &convert, sink.upcast_ref()])
                .unwrap();
            input.link(&decoder).unwrap();
            convert.link(&sink).unwrap();
            let target = convert.static_pad("sink").unwrap();
            decoder.connect_pad_added(move |_, pad| {
                if pad.name().starts_with("video_") && !target.is_linked() {
                    pad.link(&target).unwrap();
                }
            });
            pipeline.set_state(gst::State::Playing).unwrap();
            let mut count = 0;
            let mut previous = None;
            loop {
                let Some(sample) = sink.try_pull_sample(gst::ClockTime::from_seconds(10)) else {
                    assert!(sink.is_eos(), "decode timeout for {}", file.display());
                    break;
                };
                let buffer = sample.buffer().unwrap();
                let pts = buffer.pts().unwrap();
                if let Some(previous) = previous {
                    assert!(pts >= previous, "out-of-order decoded frame");
                }
                previous = Some(pts);
                let info = gstreamer_video::VideoInfo::from_caps(sample.caps().unwrap()).unwrap();
                assert_eq!((info.width(), info.height()), (64, 48));
                let bytes = buffer.map_readable().unwrap();
                assert_eq!(bytes.len(), 64 * 48 * 4);
                for pixel in bytes.chunks_exact(4) {
                    assert!(
                        pixel[0] >= 245 && pixel[1] <= 10 && pixel[2] <= 10 && pixel[3] == 255,
                        "incorrect red pixel: {pixel:?}"
                    );
                }
                count += 1;
            }
            pipeline.set_state(gst::State::Null).unwrap();
            assert_eq!(count, 6, "frame count for {}", file.display());
        }
    }
}
