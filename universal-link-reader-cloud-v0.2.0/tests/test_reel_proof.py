"""Regression tests for the fixed-Reel image-build proof."""
import json
import shutil
import subprocess
from urllib.error import URLError
import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app import main
from scripts import build_reel_proof as proof

def test_unbuilt_proof_fail_closed(monkeypatch,tmp_path):
    monkeypatch.setattr(main,'PROOF',tmp_path/'missing')
    c=TestClient(main.app)
    assert c.get('/proof/Db5BK0xCh6b/status.json').json()['status']=='not_built'
    for path in ('','/report.pdf','/contact.jpg','/meta.json','/frame/frame_01.jpg'):
        resp=c.get('/proof/Db5BK0xCh6b'+path)
        assert resp.status_code==503 and resp.headers['Cache-Control']=='no-store'
    assert c.get('/proof/Db5BK0xCh6c/status.json').status_code==404

def test_failed_network_no_stale_report(monkeypatch,tmp_path):
    out=tmp_path/'proof'
    out.mkdir()
    (out/'report.pdf').write_bytes(b'%PDF-old')
    def fail(url): raise URLError('secret-key-is-not-public')
    monkeypatch.setattr(proof,'instagram_media_source',fail)
    result=proof.build(out)
    assert result['status']=='unavailable' and result['failed_stage']=='public_page'
    assert result['error_code']=='network_error'
    assert not (out/'report.pdf').exists()
    assert 'secret-key-is-not-public' not in (out/'status.json').read_text()

def test_only_fixed_reel(tmp_path):
    with pytest.raises(ValueError):
        proof.build(tmp_path/'no','https://www.instagram.com/reel/Db5BK0xCh6c/')
    assert not (tmp_path/'no').exists()

def test_exact_post_fail_closed(monkeypatch,tmp_path):
    monkeypatch.setattr(proof,'instagram_media_source',lambda url: ('https://a.fbcdn.net/a.mp4',{'shortcode':proof.SHORTCODE,'exact_post_matched':False}))
    result=proof.build(tmp_path/'proof')
    assert result['status']=='unavailable' and result['failed_stage']=='public_page'

def test_real_video_decode_to_pdf(monkeypatch,tmp_path):
    movie=tmp_path/'movie.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin',
                    '-f','lavfi','-i','testsrc2=size=192x128:rate=6',
                    '-t','3','-c:v','libx264','-pix_fmt','yuv420p',
                    '-y',str(movie)],check=True,timeout=30)
    def page(url):
        assert url==proof.SOURCE
        return ('https://sub.fbcdn.net/file.mp4',{'shortcode':proof.SHORTCODE,'exact_post_matched':True})
    def download(url,dest,kind,limit):
        assert kind=='meta' and limit==proof.MAX_DOWNLOAD
        shutil.copyfile(movie,dest)
        return dest.stat().st_size
    monkeypatch.setattr(proof,'instagram_media_source',page)
    monkeypatch.setattr(proof,'fetch_mp4',download)
    monkeypatch.setattr(main,'PROOF',tmp_path/'proof')
    result=proof.build(main.PROOF)
    assert result['status']=='frames_ready' and result['frames_extracted']==6
    assert not (main.PROOF/'input.mp4').exists()
    c=TestClient(main.app)
    pdf=c.get('/proof/Db5BK0xCh6b/report.pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
    assert pdf.headers['X-Robots-Tag']=='noindex, nofollow'
    assert len(pdf.content)<4_500_000
    contact=c.get('/proof/Db5BK0xCh6b/contact.jpg')
    assert Image.open(io.BytesIO(contact.content)).width>100
    info=c.get('/proof/Db5BK0xCh6b/meta.json').json()
    assert len(info['source_sha256'])==64 and info['frames_opened_by_model'] is False
    assert c.get('/healthz').json()['reel_proof_status']=='frames_ready'

def test_no_sensitive_runtime_error_echo():
    result=proof.public_error(RuntimeError('secret-media-url=123'))
    assert 'secret-media-url' not in json.dumps(result)
