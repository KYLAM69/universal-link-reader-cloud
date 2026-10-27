"""Bounded public media retrieval and local video decoding."""
from __future__ import annotations
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .security import (
    RejectedURL, IG_HOSTS, canonical_instagram, validate_meta_cdn_url,
    validate_mp4_url, validate_public_dns,
)

MAX_HTML = 3 * 1024 * 1024
USER_AGENT = 'UniversalLinkReaderCloudPoC/0.2.0 (public pages, no login)'


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, kind: str, shortcode: str | None = None):
        super().__init__()
        self.kind, self.shortcode, self.n = kind, shortcode, 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.n += 1
        if self.n > 3:
            raise RejectedURL('redirect_limit')
        if self.kind == 'instagram':
            _, redirected_code = canonical_instagram(newurl)
            if redirected_code != self.shortcode:
                raise RejectedURL('instagram_redirect_post_id_mismatch')
        elif self.kind == 'meta':
            validate_meta_cdn_url(newurl)
        elif self.kind == 'mp4':
            validate_mp4_url(newurl)
        validate_public_dns(urlsplit(newurl).hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, kind: str, shortcode: str | None = None):
    host = urlsplit(url).hostname
    validate_public_dns(host)
    opener = urllib.request.build_opener(
        SafeRedirect(kind, shortcode), urllib.request.ProxyHandler({})
    )
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    return opener.open(request, timeout=15)


class EmbeddedJSON(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_json, self.chunks, self.blocks = False, [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.in_json = dict(attrs).get('type', '').lower() in ('application/json', 'application/ld+json')
            self.chunks = []

    def handle_data(self, data):
        if self.in_json:
            self.chunks.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.in_json:
            self.blocks.append(''.join(self.chunks))
            self.chunks, self.in_json = [], False


def exact_instagram_media(page_html: str, shortcode: str) -> tuple[int, list[str]]:
    parser = EmbeddedJSON()
    parser.feed(page_html)
    urls, matches = [], 0
    for block in parser.blocks:
        try:
            root = json.loads(block)
        except (ValueError, TypeError):
            continue
        stack, visited = [root], 0
        while stack and visited < 50000:
            current = stack.pop()
            visited += 1
            if isinstance(current, list):
                stack.extend(current)
            elif isinstance(current, dict):
                if current.get('shortcode') == shortcode or current.get('code') == shortcode:
                    matches += 1
                    options = current.get('video_versions') or []
                    for option in options if isinstance(options, list) else []:
                        if isinstance(option, dict) and isinstance(option.get('url'), str):
                            urls.append(option['url'])
                    if isinstance(current.get('video_url'), str):
                        urls.append(current['video_url'])
                stack.extend(value for value in current.values() if isinstance(value, (dict, list)))
    valid = []
    for url in urls:
        try:
            validate_meta_cdn_url(url)
        except RejectedURL:
            continue
        if url not in valid:
            valid.append(url)
    return matches, valid


def instagram_media_source(url: str) -> tuple[str, dict]:
    canonical, code = canonical_instagram(url)
    with _open(canonical, 'instagram', code) as response:
        if response.headers.get_content_type() != 'text/html':
            raise RuntimeError('unexpected_instagram_content_type')
        payload = response.read(MAX_HTML + 1)
        if len(payload) > MAX_HTML:
            raise RuntimeError('instagram_page_too_large')
        html = payload.decode(response.headers.get_content_charset() or 'utf-8', 'replace')
    matches, media = exact_instagram_media(html, code)
    if not media:
        raise RuntimeError('no_exact_post_declared_public_mp4')
    return media[0], {'source_kind': 'instagram', 'canonical_source': canonical,
                      'exact_post_matched': matches > 0, 'shortcode': code}


def fetch_mp4(url: str, target: Path, kind: str, max_bytes: int):
    if kind == 'meta':
        validate_meta_cdn_url(url)
    else:
        validate_mp4_url(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open(url, kind) as response:
        content_type = response.headers.get_content_type()
        if content_type not in ('video/mp4', 'application/octet-stream', 'binary/octet-stream'):
            raise RuntimeError('unexpected_video_mime_type:' + content_type)
        length = response.headers.get('Content-Length')
        if length and int(length) > max_bytes:
            raise RuntimeError('video_exceeds_max_bytes')
        size = 0
        with target.open('wb') as file:
            while True:
                chunk = response.read(min(1048576, max_bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise RuntimeError('video_exceeds_max_bytes')
                file.write(chunk)
    with target.open('rb') as movie:
        header = movie.read(24)
    if size < 1024 or b'ftyp' not in header:
        raise RuntimeError('not_an_mp4_container')
    return size


def inspect_local_video(path: Path, max_duration: float = 120.0) -> dict:
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file',
         '-show_format', '-show_streams', '-of', 'json', str(path)],
        capture_output=True, text=True, timeout=15, check=True)
    info = json.loads(result.stdout)
    video = next((stream for stream in info.get('streams', []) if stream.get('codec_type') == 'video'), None)
    if not video:
        raise RuntimeError('missing_video_stream')
    duration = float(info.get('format', {}).get('duration') or 0)
    if not (0 < duration <= max_duration):
        raise RuntimeError('duration_invalid_or_too_long')
    width = int(video.get('width') or 0)
    height = int(video.get('height') or 0)
    if width <= 0 or height <= 0 or width * height > 3840 * 2160:
        raise RuntimeError('unsupported_video_dimensions')
    return {'duration_seconds': round(duration, 3), 'width': width, 'height': height,
            'audio_stream_present': any(s.get('codec_type') == 'audio' for s in info.get('streams', []))}


def extract_frames(path: Path, root: Path, duration: float, count: int = 6) -> list[dict]:
    count = min(max(int(count), 1), 8)
    times = [max(0.05, duration * (i + .5) / count) for i in range(count)]
    frames = []
    for i, second in enumerate(times):
        destination = root / f'frame_{i+1:02d}.jpg'
        subprocess.run(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
             '-protocol_whitelist', 'file', '-ss', f'{second:.3f}', '-i', str(path), '-frames:v', '1',
             '-vf', 'scale=480:-2', '-q:v', '5', '-y', str(destination)],
            capture_output=True, timeout=20, check=True)
        if destination.exists() and destination.stat().st_size > 512:
            frames.append({'second': round(second, 2), 'filename': destination.name})
    if not frames:
        raise RuntimeError('no_decoded_frames')
    return frames


def process_mp4(src: Path, out_dir: Path, label: str, frame_count: int = 6, max_duration: int = 120) -> dict:
    video_info = inspect_local_video(src, max_duration=max_duration)
    frames = extract_frames(src, out_dir, video_info['duration_seconds'], frame_count)
    return {'label': label, 'video_verified': True, 'frames_extracted': len(frames),
            'frames_opened_by_model': False, 'audio_transcribed': False,
            'frames': frames, **video_info}
