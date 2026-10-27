"""Small, deliberately bounded cloud proof-of-concept, NOT a public scraping proxy."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import escape
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from threading import Lock
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .media import fetch_mp4, instagram_media_source, process_mp4
from .reporting import prepare_outputs, render_html
from .security import RejectedURL, IG_HOSTS, canonical_instagram, validate_mp4_url

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'static' / 'demo'
PROOF_SHORTCODE = 'Db5BK0xCh6b'
PROOF = ROOT / 'static' / 'reel-proof' / PROOF_SHORTCODE
PROOF_ROOT = '/proof/' + PROOF_SHORTCODE
DATA_ROOT = Path(os.environ.get('ULR_DATA_ROOT', '/tmp/ulr_reports'))
RELEASE = '0.3.0'
app = FastAPI(title='Universal Link Reader Cloud Proof of Concept', version=RELEASE,
              docs_url=None, redoc_url=None)

pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ulr_worker')
jobs: dict[str, dict] = {}
lock = Lock()
accepted_total = 0


def flag(name: str) -> bool:
    return os.environ.get(name, '0').strip() == '1'


def _ttl() -> int:
    return max(60, min(int(os.environ.get('ULR_JOB_TTL_SECONDS', '1800')), 7200))


def prune():
    now = time.time()
    with lock:
        expired = [key for key, value in jobs.items() if value['created_at'] + _ttl() < now and value['state'] != 'running']
        for key in expired:
            jobs.pop(key)
            shutil.rmtree(DATA_ROOT / key, ignore_errors=True)


def _token_allowed(authorization: str | None) -> bool:
    secret = os.environ.get('ULR_ADMIN_TOKEN', '')
    return bool(len(secret) >= 24 and authorization and authorization.startswith('Bearer ')
                and hmac.compare_digest(authorization[7:], secret))


def _job_run(job_id: str, original_url: str, kind: str):
    target = DATA_ROOT / job_id
    target.mkdir(parents=True, exist_ok=True)
    with lock:
        jobs[job_id]['state'] = 'running'
    try:
        if kind == 'instagram':
            media_url, details = instagram_media_source(original_url)
            remote_kind = 'meta'
        else:
            media_url, details = original_url, {'source_kind': 'public_mp4', 'canonical_source': original_url}
            remote_kind = 'mp4'
        video = target / 'input.mp4'
        size = fetch_mp4(media_url, video, remote_kind,
                         max(1024 * 1024, min(int(os.environ.get('ULR_MAX_VIDEO_BYTES', '41943040')), 80 * 1024 * 1024)))
        result = process_mp4(video, target, original_url[:100], frame_count=6, max_duration=120)
        result.update(source_kind=details['source_kind'], source_url=details['canonical_source'],
                      exact_post_matched=details.get('exact_post_matched', False),
                      status='frames_ready', downloaded_bytes=size)
        manifest = prepare_outputs(target, result)
        with lock:
            jobs[job_id].update(state='ready', result=manifest)
    except Exception as exc:
        # Avoid reflecting arbitrary exception strings (could contain signed media URLs).
        if isinstance(exc, RejectedURL):
            code = str(exc)[:70]
        elif isinstance(exc, (RuntimeError, ValueError)):
            allowed_prefixes = ('no_exact_post_declared_public_mp4', 'unexpected_video_mime_type',
                                'video_exceeds_max_bytes', 'not_an_mp4_container',
                                'duration_invalid_or_too_long', 'missing_video_stream',
                                'unsupported_video_dimensions', 'no_decoded_frames',
                                'unexpected_instagram_content_type', 'instagram_page_too_large')
            raw = str(exc)
            code = raw[:80] if raw.startswith(allowed_prefixes) else 'processing_error'
        else:
            code = 'network_or_processing_failure'
        with lock:
            jobs[job_id].update(state='failed', error_type=type(exc).__name__, error_code=code)
    finally:
        (target / 'input.mp4').unlink(missing_ok=True)


class JobRequest(BaseModel):
    url: str = Field(min_length=12, max_length=4096)


DEMO_ASSETS = {'/demo/report.pdf', '/demo/contact.jpg', '/demo/meta.json'}
DEMO_FRAME_PATH = re.compile(r'/demo/frame/frame_[0-9]{2}\.jpg\Z')
PROOF_ALLOWED = frozenset({PROOF_ROOT, PROOF_ROOT + '/status.json',
                           PROOF_ROOT + '/report.pdf', PROOF_ROOT + '/contact.jpg',
                           PROOF_ROOT + '/meta.json'})
PROOF_FRAME_PATH = re.compile(r'/proof/Db5BK0xCh6b/frame/frame_[0-9]{2}\.jpg\Z')

@app.middleware('http')
async def visibility_and_cache_headers(request: Request, call_next):
    result = await call_next(request)
    path = request.url.path
    result.headers['X-Content-Type-Options'] = 'nosniff'
    if not 200 <= result.status_code < 300:
        result.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        result.headers['Cache-Control'] = 'no-store'
    elif path in ('/', '/demo') or path in DEMO_ASSETS or DEMO_FRAME_PATH.fullmatch(path):
        if 'X-Robots-Tag' in result.headers:
            del result.headers['X-Robots-Tag']
        result.headers['Cache-Control'] = 'public, max-age=60, s-maxage=300'
    elif path == '/robots.txt':
        if 'X-Robots-Tag' in result.headers:
            del result.headers['X-Robots-Tag']
        result.headers['Cache-Control'] = 'public, max-age=300'
    elif path in PROOF_ALLOWED or PROOF_FRAME_PATH.fullmatch(path):
        result.headers['X-Robots-Tag'] = 'noindex, nofollow'
        result.headers['Cache-Control'] = 'public, max-age=30, s-maxage=300'
    elif path == '/healthz':
        result.headers['X-Robots-Tag'] = 'noindex, nofollow'
        result.headers['Cache-Control'] = 'public, max-age=15'
    else:
        result.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        result.headers['Cache-Control'] = 'no-store'
    return result

@app.get('/robots.txt', response_class=PlainTextResponse)
def robots_txt():
    return 'User-agent: *\nAllow: /demo\nDisallow: /api/\nDisallow: /r/\n'


@app.get('/healthz')
def health():
    return {'status': 'ok', 'version': RELEASE, 'demo_available': (DEMO / 'report.pdf').is_file(),
            'reel_proof_status': _proof_status().get('status', 'not_built')}


@app.get('/', response_class=HTMLResponse)
def home():
    return '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui;max-width:720px;margin:3rem auto;padding:0 1rem">
<h1>Universal Link Reader Cloud PoC v0.3.0</h1>
<p>Proof of concept only. No MCP needed to READ public output. Creating new jobs requires a private admin token; remote jobs are disabled by default.</p>
<p><a href="/demo">Open synthetic video frame report</a> | <a href="/demo/report.pdf">PDF</a> | <a href="/demo/meta.json">JSON</a></p>
<p>This synthetic demonstration does not prove Instagram access from a cloud host.</p>
<p><a href="/proof/Db5BK0xCh6b">Experimental exact-Reel cloud-build result</a> | <a href="/proof/Db5BK0xCh6b/status.json">Build diagnostic</a></p></body></html>'''


@app.get('/demo', response_class=HTMLResponse)
def demo():
    manifest = DEMO / 'metadata.json'
    if not manifest.exists():
        raise HTTPException(status_code=503, detail='synthetic_demo_not_built')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    return HTMLResponse(render_html(data, '/demo', demo=True))


@app.get('/demo/report.pdf')
def demo_pdf():
    return _get_asset(DEMO, 'report.pdf', 'application/pdf')


@app.get('/demo/contact.jpg')
def demo_contact():
    return _get_asset(DEMO, 'contact.jpg', 'image/jpeg')


@app.get('/demo/frame/{file_name}')
def demo_frame(file_name: str):
    if not file_name.startswith('frame_') or not file_name.endswith('.jpg'):
        raise HTTPException(status_code=404)
    return _get_asset(DEMO, file_name, 'image/jpeg')


@app.get('/demo/meta.json')
def demo_json():
    return _get_asset(DEMO, 'metadata.json', 'application/json')


def _get_asset(directory: Path, name: str, media_type: str):
    if name not in ('report.pdf', 'contact.jpg', 'metadata.json') and not (name.startswith('frame_') and name.endswith('.jpg') and len(name) == 12):
        raise HTTPException(status_code=404)
    file = directory / name
    if not file.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(file, media_type=media_type)




def _proof_status():
    status_file = PROOF / 'status.json'
    if not status_file.is_file():
        return {'status': 'not_built', 'shortcode': PROOF_SHORTCODE}
    try:
        return json.loads(status_file.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {'status': 'invalid_build_status', 'shortcode': PROOF_SHORTCODE}


def _proof_ready():
    state = _proof_status()
    if state.get('status') != 'frames_ready' or not (PROOF / 'report.pdf').is_file():
        raise HTTPException(status_code=503, detail={
            'error': 'reel_proof_unavailable',
            'status_url': PROOF_ROOT + '/status.json',
            'failed_stage': state.get('failed_stage', 'build_not_ready')})
    return state


@app.get('/proof/Db5BK0xCh6b', response_class=HTMLResponse)
def proof_landing():
    status = _proof_status()
    if status.get('status') != 'frames_ready':
        stage = escape(status.get('failed_stage', 'not_built'))
        return HTMLResponse('<h1>Instagram cloud proof unavailable</h1>'
                            '<p>Cloud build did not obtain verified video frames. '
                            'This does not imply the Reel is private or deleted.</p>'
                            f'<p>Stage: {stage}</p>'
                            f'<p><a href="{PROOF_ROOT}/status.json">Build diagnostic JSON</a></p>',
                            status_code=503)
    manifest = json.loads((PROOF / 'metadata.json').read_text(encoding='utf8'))
    return HTMLResponse(render_html(manifest, PROOF_ROOT, demo=False))


@app.get('/proof/Db5BK0xCh6b/status.json')
def proof_status():
    return JSONResponse(_proof_status())


@app.get('/proof/Db5BK0xCh6b/meta.json')
def proof_meta():
    _proof_ready()
    return _get_asset(PROOF, 'metadata.json', 'application/json')


@app.get('/proof/Db5BK0xCh6b/report.pdf')
def proof_pdf():
    _proof_ready()
    return _get_asset(PROOF, 'report.pdf', 'application/pdf')


@app.get('/proof/Db5BK0xCh6b/contact.jpg')
def proof_contact():
    _proof_ready()
    return _get_asset(PROOF, 'contact.jpg', 'image/jpeg')


@app.get('/proof/Db5BK0xCh6b/frame/{file_name}')
def proof_frame(file_name: str):
    _proof_ready()
    if not re.fullmatch(r'frame_[0-9]{2}\.jpg', file_name):
        raise HTTPException(status_code=404)
    return _get_asset(PROOF, file_name, 'image/jpeg')


@app.post('/api/jobs', status_code=202)
def new_job(payload: JobRequest, authorization: str | None = Header(default=None)):
    global accepted_total
    if not flag('ULR_ENABLE_REMOTE_JOBS'):
        raise HTTPException(status_code=503, detail='remote_jobs_disabled')
    if not _token_allowed(authorization):
        raise HTTPException(status_code=401, detail='valid_bearer_token_required')
    url = payload.url
    try:
        if (urlsplit(url).hostname or '').lower() in IG_HOSTS:
            if not flag('ULR_ENABLE_INSTAGRAM'):
                raise RejectedURL('instagram_experiment_disabled')
            canonical, _ = canonical_instagram(url)
            kind = 'instagram'
        else:
            canonical = validate_mp4_url(url)
            kind = 'mp4'
    except (RejectedURL, ValueError):
        raise HTTPException(status_code=400, detail='unsupported_or_unsafe_source_url')
    prune()
    with lock:
        if sum(1 for j in jobs.values() if j['state'] in ('queued', 'running')) >= 1:
            raise HTTPException(status_code=429, detail='one_job_at_a_time')
        if accepted_total >= 12:
            raise HTTPException(status_code=429, detail='max_jobs_per_service_boot_reached')
        accepted_total += 1
        job_id = secrets.token_urlsafe(24)
        jobs[job_id] = {'state': 'queued', 'created_at': time.time(), 'kind': kind}
    pool.submit(_job_run, job_id, canonical, kind)
    return {'status': 'queued', 'result_url': f'/r/{job_id}',
            'status_url': f'/r/{job_id}/meta.json', 'note': 'Opaque result URLs are publicly accessible to anyone who has the link.'}


def _get_job(job_id: str):
    prune()
    with lock:
        data = jobs.get(job_id)
        return dict(data) if data else None


@app.get('/r/{job_id}', response_class=HTMLResponse)
def job_html(job_id: str):
    data = _get_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail='unknown_or_expired')
    if data['state'] == 'failed':
        return HTMLResponse(f'<h1>Job failed</h1><p>{escape(data.get("error_type", ""))}: {escape(data.get("error_code", ""))}</p>', status_code=422)
    if data['state'] != 'ready':
        return HTMLResponse(f'<h1>Processing: {escape(data["state"])}</h1><p>Reload this page.</p>', status_code=202)
    return HTMLResponse(render_html(data['result'], f'/r/{job_id}'))


@app.get('/r/{job_id}/meta.json')
def job_meta(job_id: str):
    data = _get_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail='unknown_or_expired')
    if data['state'] == 'ready':
        return data['result']
    if data['state'] == 'failed':
        return JSONResponse({'state': 'failed', 'error_type': data.get('error_type'), 'error_code': data.get('error_code')}, status_code=422)
    return JSONResponse({'state': data['state']}, status_code=202)


@app.get('/r/{job_id}/report.pdf')
def job_pdf(job_id: str):
    _assert_job_ready(job_id)
    return _get_asset(DATA_ROOT / job_id, 'report.pdf', 'application/pdf')


@app.get('/r/{job_id}/contact.jpg')
def job_contact(job_id: str):
    _assert_job_ready(job_id)
    return _get_asset(DATA_ROOT / job_id, 'contact.jpg', 'image/jpeg')


@app.get('/r/{job_id}/frame/{file_name}')
def job_frame(job_id: str, file_name: str):
    _assert_job_ready(job_id)
    return _get_asset(DATA_ROOT / job_id, file_name, 'image/jpeg')


def _assert_job_ready(job_id: str):
    data = _get_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail='unknown_or_expired')
    if data['state'] != 'ready':
        raise HTTPException(status_code=409, detail='job_not_ready')
