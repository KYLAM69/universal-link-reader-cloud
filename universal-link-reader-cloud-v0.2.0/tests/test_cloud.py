from pathlib import Path
from unittest.mock import patch
import io
import json
import os
import subprocess
import tempfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.media import exact_instagram_media, inspect_local_video, process_mp4
from app.reporting import prepare_outputs
from app.security import (
    RejectedURL, canonical_instagram, validate_meta_cdn_url,
    validate_mp4_url, validate_public_dns,
)


@pytest.fixture(scope='module')
def client():
    return TestClient(main.app)


@pytest.mark.parametrize('url', [
    'http://interactive-examples.mdn.mozilla.net/media/flower.mp4',
    'https://127.0.0.1/flower.mp4',
    'https://localhost/flower.mp4',
    'https://bad.example/flower.mp4',
    'https://interactive-examples.mdn.mozilla.net/flower.jpg',
    'https://interactive-examples.mdn.mozilla.net/flower.mp4?token=secret',
    'https://user:secret@interactive-examples.mdn.mozilla.net/flower.mp4',
    'https://interactive-examples.mdn.mozilla.net:8443/flower.mp4',
])
def test_bad_urls_rejected(url):
    with pytest.raises(RejectedURL):
        validate_mp4_url(url)


def test_approved_mp4_url():
    assert validate_mp4_url('https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4')


def test_exact_instagram_identity():
    url, code = canonical_instagram('https://www.instagram.com/reel/Db5BK0xCh6b/')
    assert code == 'Db5BK0xCh6b'
    assert url.endswith('/Db5BK0xCh6b/')
    with pytest.raises(RejectedURL):
        canonical_instagram('https://www.instagram.com/reel/Db5BK0xCh6c/?token=1')


def test_video_candidate_requires_matching_post_id():
    text = '''<script type="application/json">{
    "items":[{"code":"wrong123","video_url":"https://a.fbcdn.net/wrong.mp4"},
    {"code":"Db5BK0xCh6b","video_versions":[{"url":"https://a.fbcdn.net/correct.mp4"}]}]
    }</script>'''
    count, urls = exact_instagram_media(text, 'Db5BK0xCh6b')
    assert count == 1
    assert urls == ['https://a.fbcdn.net/correct.mp4']
    count, urls = exact_instagram_media(text, 'Db5BK0xCh6c')
    assert count == 0 and not urls


def test_meta_cdn_rejects_spoof():
    assert validate_meta_cdn_url('https://a.fbcdn.net/path')
    with pytest.raises(RejectedURL):
        validate_meta_cdn_url('https://a.fbcdn.net.evil.org/path')


def test_dns_rejects_private_address():
    with patch('app.security.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
        with pytest.raises(RejectedURL, match='non_public_dns_address'):
            validate_public_dns('interactive-examples.mdn.mozilla.net')


def test_no_remote_jobs_by_default(client, monkeypatch):
    monkeypatch.delenv('ULR_ENABLE_REMOTE_JOBS', raising=False)
    response = client.post('/api/jobs', json={'url': 'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'})
    assert response.status_code == 503


def test_authorization_requires_real_token(client, monkeypatch):
    monkeypatch.setenv('ULR_ENABLE_REMOTE_JOBS', '1')
    monkeypatch.delenv('ULR_ADMIN_TOKEN', raising=False)
    response = client.post('/api/jobs', json={'url': 'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'})
    assert response.status_code == 401
    monkeypatch.setenv('ULR_ADMIN_TOKEN', 'a' * 28)
    response = client.post('/api/jobs', json={'url': 'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'}, headers={'Authorization': 'Bearer wrong'})
    assert response.status_code == 401


def test_block_unsafe_source_after_auth(client, monkeypatch):
    monkeypatch.setenv('ULR_ENABLE_REMOTE_JOBS', '1')
    monkeypatch.setenv('ULR_ADMIN_TOKEN', 'a' * 28)
    response = client.post('/api/jobs', json={'url': 'https://127.0.0.1/private.mp4'}, headers={'Authorization': 'Bearer ' + 'a' * 28})
    assert response.status_code == 400


def test_instagram_gate(client, monkeypatch):
    monkeypatch.setenv('ULR_ENABLE_REMOTE_JOBS', '1')
    monkeypatch.setenv('ULR_ADMIN_TOKEN', 'a' * 28)
    monkeypatch.delenv('ULR_ENABLE_INSTAGRAM', raising=False)
    response = client.post('/api/jobs', json={'url': 'https://www.instagram.com/reel/Db5BK0xCh6b/'}, headers={'Authorization': 'Bearer ' + 'a' * 28})
    assert response.status_code == 400


def test_health_and_home(client):
    assert client.get('/healthz').status_code == 200
    assert 'v0.2.0' in client.get('/').text
    assert client.get('/demo').status_code == 200
    assert client.get('/demo/report.pdf').status_code == 200
    assert client.get('/demo/report.pdf').content.startswith(b'%PDF')
    response = client.get('/demo/contact.jpg')
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).width > 200
    data = client.get('/demo/meta.json').json()
    assert data['frames_extracted'] == 4
    assert data['video_verified'] is True
    assert data['frames_opened_by_model'] is False
    assert client.get('/demo/frame/../../secret').status_code == 404
    assert client.get('/demo/frame/secrets.jpg').status_code == 404
    assert response.headers.get('X-Robots-Tag') == 'noindex, nofollow, noarchive'


def test_unknown_job(client):
    response = client.get('/r/unknown-long-random-id')
    assert response.status_code == 404


def test_end_to_end_generated_video_and_pdf(tmp_path):
    path = tmp_path / 'clip.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                    '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=4',
                    '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-y', str(path)], check=True, timeout=20)
    info = inspect_local_video(path)
    assert info['duration_seconds'] >= 1.9
    data = process_mp4(path, tmp_path, 'Synthetic unit test', frame_count=4)
    data.update(source_kind='synthetic', source_url='local_test')
    data = prepare_outputs(tmp_path, data)
    assert data['video_verified'] and data['frames_extracted'] == 4
    assert (tmp_path / 'report.pdf').read_bytes()[:4] == b'%PDF'
    assert (tmp_path / 'contact.jpg').stat().st_size > 2000
    assert all(Image.open(tmp_path / frame['filename']).width > 50 for frame in data['frames'])


def test_authorized_async_job_complete_roundtrip(client, monkeypatch, tmp_path):
    """Mock network transport only; real MP4 decode, output files and HTTP are exercised."""
    import shutil
    import time
    sample = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                    '-f', 'lavfi', '-i', 'testsrc2=size=192x128:rate=5',
                    '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-y', str(sample)], check=True, timeout=20)

    def stub_download(url, target, kind, limit):
        shutil.copy2(sample, target)
        return target.stat().st_size

    monkeypatch.setattr(main, 'DATA_ROOT', tmp_path / 'private_jobs')
    monkeypatch.setattr(main, 'accepted_total', 0)
    monkeypatch.setenv('ULR_ENABLE_REMOTE_JOBS', '1')
    monkeypatch.setenv('ULR_ADMIN_TOKEN', 'b' * 32)
    monkeypatch.setattr(main, 'fetch_mp4', stub_download)
    url = 'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'
    response = client.post('/api/jobs', json={'url': url},
                           headers={'Authorization': 'Bearer ' + 'b' * 32})
    assert response.status_code == 202, response.text
    root_url = response.json()['result_url']
    assert root_url.startswith('/r/')
    for _ in range(100):
        info = client.get(root_url + '/meta.json')
        if info.status_code == 200 or info.status_code >= 400:
            break
        time.sleep(.07)
    assert info.status_code == 200, info.text
    assert info.json()['video_verified'] is True
    assert info.json()['frames_extracted'] == 6
    assert client.get(root_url + '/report.pdf').content.startswith(b'%PDF')
    assert client.get(root_url + '/contact.jpg').status_code == 200
    assert 'Sampled frames do not establish' in client.get(root_url).text
    assert 'does not prove ChatGPT opened' in client.get(root_url).text
