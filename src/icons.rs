//! App icons, served from BariStore's own cache: each one is fetched once,
//! shrunk to 120 px (PNG/JPEG; other formats are kept as they are) and kept
//! in ~/.baristore/icons. Sources link icons of up to several megabytes on
//! slow hosts; the page then loads them from the iPhone itself.

use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::time::Duration;

use crate::{logf, state};

const SIZE: u32 = 120;
const MAX_BYTES: usize = 8 << 20;

fn path(url: &str) -> PathBuf {
    state::path("icons").join(hex::encode(&Sha256::digest(url.as_bytes())[..12]))
}

/// The MIME type of image bytes, from their magic number.
pub fn kind(bytes: &[u8]) -> &'static str {
    match bytes {
        [0x89, b'P', b'N', b'G', ..] => "image/png",
        [0xFF, 0xD8, ..] => "image/jpeg",
        [b'G', b'I', b'F', ..] => "image/gif",
        [b'R', b'I', b'F', b'F', _, _, _, _, b'W', b'E', b'B', b'P', ..] => "image/webp",
        _ if bytes.windows(4).take(256).any(|w| w == b"<svg") => "image/svg+xml",
        _ => "application/octet-stream",
    }
}

fn shrink(bytes: &[u8]) -> Option<Vec<u8>> {
    // Bounded: Minis's shell has little memory, and iOS takes some back in
    // the background. A bigger image is kept as it is.
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(2048);
    limits.max_image_height = Some(2048);
    limits.max_alloc = Some(24 << 20);
    let mut reader = image::ImageReader::new(std::io::Cursor::new(bytes)).with_guessed_format().ok()?;
    reader.limits(limits);
    let img = reader.decode().ok()?;
    if img.width() <= SIZE && img.height() <= SIZE {
        return None;
    }
    let mut out = std::io::Cursor::new(Vec::new());
    img.thumbnail(SIZE, SIZE).write_to(&mut out, image::ImageFormat::Png).ok()?;
    Some(out.into_inner())
}

pub fn cached(url: &str) -> Option<Vec<u8>> {
    std::fs::read(path(url)).ok().filter(|b| !b.is_empty())
}

/// The icon at `url`, from the cache or fetched (and cached) now.
pub async fn get(http: &reqwest::Client, url: &str) -> Result<Vec<u8>, String> {
    if let Some(bytes) = cached(url) {
        return Ok(bytes);
    }
    if !url.starts_with("https://") && !url.starts_with("http://") {
        return Err("not a web address".into());
    }
    let resp = http.get(url).timeout(Duration::from_secs(30)).send().await
        .and_then(|r| r.error_for_status()).map_err(|e| e.to_string())?;
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    if bytes.len() > MAX_BYTES || kind(&bytes) == "application/octet-stream" {
        return Err("not an image".into());
    }
    let original = bytes.to_vec();
    let small = tokio::task::spawn_blocking(move || shrink(&original)).await.ok().flatten();
    let stored = small.unwrap_or_else(|| bytes.to_vec());
    let file = path(url);
    if let Some(dir) = file.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(&file, &stored);
    Ok(stored)
}

/// Fetches the icons not cached yet, two at a time (memory), so opening the
/// store later shows them at once.
pub async fn prefetch(urls: Vec<String>) {
    use futures_util::StreamExt;
    let http = crate::store::client();
    let missing: Vec<String> = urls.into_iter().filter(|u| !u.is_empty() && cached(u).is_none()).collect();
    if missing.is_empty() {
        return;
    }
    let started = std::time::Instant::now();
    let count = missing.len();
    futures_util::stream::iter(missing)
        .for_each_concurrent(2, |url| {
            let http = http.clone();
            async move {
                if let Err(e) = get(&http, &url).await {
                    logf!("icons: {url}: {e}");
                }
            }
        })
        .await;
    logf!("icons: {count} fetched in {:.1} s", started.elapsed().as_secs_f64());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn big_png_is_shrunk_and_small_one_kept() {
        let mut big = std::io::Cursor::new(Vec::new());
        image::RgbaImage::from_pixel(512, 512, image::Rgba([20, 60, 200, 255]))
            .write_to(&mut big, image::ImageFormat::Png).unwrap();
        let small = shrink(big.get_ref()).expect("shrunk");
        assert_eq!(kind(&small), "image/png");
        let img = image::load_from_memory(&small).unwrap();
        assert_eq!((img.width(), img.height()), (SIZE, SIZE));
        let mut tiny = std::io::Cursor::new(Vec::new());
        image::RgbaImage::from_pixel(64, 64, image::Rgba([0, 0, 0, 255]))
            .write_to(&mut tiny, image::ImageFormat::Png).unwrap();
        assert!(shrink(tiny.get_ref()).is_none());
    }
}
