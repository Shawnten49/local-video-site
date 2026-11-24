# Site resolution architecture

The downloader uses a shared media pipeline with optional site profiles.

## Layers

1. `local_video_site.integrations.profiles.registry` resolves page, media, and actor-profile URLs to a
   `SiteProfile`.
2. `local_video_site.integrations.browser` owns Chrome/CDP lifecycle and request
   blocking shared by metadata and avatar adapters.
3. `local_video_site.downloader.sniffer` collects and normalizes generic media candidates.
4. `local_video_site.downloader.resolution` exposes the media catalog and quality
   selection policy.
5. `local_video_site.downloader.browser_hls` downloads HLS through a generic transport.
   Site profiles may provide stable URL and master-playlist discovery policies.
6. `local_video_site.integrations.metadata` executes a profile's metadata
   factory.

## Data and download sources

`metadata_url` and `download_url` are resolved independently. Metadata such as
title, categories, and actors comes from the metadata profile. Media quality and
the downloaded file come from the download profile.

## Adding a site

1. Add a `SiteProfile` under `local_video_site/integrations/profiles/`.
2. Register it in `local_video_site/integrations/profiles/registry.py`.
3. Add a metadata factory only when the site exposes categories or actors.
4. Add HLS policy functions only when the CDN differs from the generic policy.
5. Add HTML/JSON fixtures and contract tests.

The generic network, DOM source, performance, quality, download, retry,
checkpoint, and logging pipelines must remain usable when the site profile has no
custom media logic.

## Signed media URL recovery

Signed media URLs are treated as replaceable transport credentials. When a
download reaches the URL expiry margin or receives `401`, `403`, or `410`, the
transport stops without deleting resumable artifacts. The resolver reopens the
download page, selects the same declared quality, and compares the stable media
identity before continuing.

Direct MP4 files resume through yt-dlp with `continuedl=True`. HLS files reuse
the existing segment checkpoint after validating the refreshed playlist
signature. Partial artifacts above 256 MiB are preserved when ordinary retries
are exhausted instead of being automatically deleted.

## HLS concurrency

HLS segment batches start with two browser workers and ramp through three to a
maximum of four after clean batches. After CDN throttling, concurrency drops to
one and recovers through `1 -> 2 -> 3 -> 4`; returning to four requires six clean
batches and no rate-limit event during the five-minute protection window.

The generic sniffer also reads current-page player configuration objects that
pair media `*_url` values with their `*_url_text` quality labels. This discovers
alternate renditions without triggering every quality switch in the player.
Direct Range probing falls back from httpx to curl_cffi Chrome impersonation
when a CDN rejects the default TLS handshake.
