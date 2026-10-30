fn main() {
    if std::env::var_os("CARGO_FEATURE_STATIC").is_some() {
        for name in ["gstcoreelements", "gstapp", "gstvideoconvertscale"] {
            pkg_config::Config::new().statik(true).probe(name).unwrap();
        }
    }
}
