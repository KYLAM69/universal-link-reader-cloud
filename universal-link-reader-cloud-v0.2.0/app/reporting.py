"""Generate reproducible visual evidence; frames are not an audio transcript."""
from __future__ import annotations
from html import escape
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def build_contact_sheet(root: Path, data: dict) -> Path:
    frames = data['frames']
    tile_w, tile_h = 360, 560
    columns, rows = 2, (len(frames) + 1) // 2
    board = Image.new('RGB', (columns * tile_w, rows * tile_h), '#17202b')
    draw = ImageDraw.Draw(board)
    for index, frame in enumerate(frames):
        source = Image.open(root / frame['filename']).convert('RGB')
        source.thumbnail((tile_w - 12, tile_h - 60))
        x, y = (index % 2) * tile_w, (index // 2) * tile_h
        dx = x + (tile_w - source.width) // 2
        dy = y + 8 + (tile_h - 60 - source.height) // 2
        board.paste(source, (dx, dy))
        draw.text((x + 10, y + tile_h - 38),
                  f"Time: {frame['second']:.2f} s  |  Frame {index + 1}", fill='white')
    target = root / 'contact.jpg'
    board.save(target, quality=76, optimize=True)
    return target


def build_pdf(root: Path, data: dict) -> Path:
    """Landscape A4 pages, max 4 frames per page; use thumbnails in report."""
    target = root / 'report.pdf'
    document = canvas.Canvas(str(target), pagesize=(842, 595), pageCompression=1)
    label = data.get('label', 'Public video analysis')[:100]
    source = data.get('source_url', 'Synthetic sample')[:125]
    frames = data['frames']
    for base in range(0, len(frames), 4):
        document.setFont('Helvetica-Bold', 17)
        document.drawString(28, 563, 'Universal Link Reader | Verified frame report')
        document.setFont('Helvetica', 10)
        document.drawString(28, 543, f'Source type: {data.get("source_kind", "demo")[:30]}  |  Duration: {data["duration_seconds"]:.2f}s')
        document.drawString(28, 528, f'Label: {label.encode("ascii", "replace").decode("ascii")}')
        document.drawString(28, 513, f'Source: {source.encode("ascii", "replace").decode("ascii")}')
        for offset, frame in enumerate(frames[base:base + 4]):
            column, row = offset % 2, offset // 2
            x, y = 28 + column * 403, 287 - row * 239
            path = root / frame['filename']
            with Image.open(path) as image:
                width, height = image.size
            limit_w, limit_h = 390.0, 212.0
            scale = min(limit_w / width, limit_h / height)
            actual_w, actual_h = width * scale, height * scale
            document.drawImage(ImageReader(str(path)), x + (390 - actual_w)/2,
                               y + (limit_h - actual_h) / 2,
                               width=actual_w, height=actual_h)
            document.setFont('Helvetica-Bold', 10)
            document.drawString(x + 8, y - 16, f"Frame {base + offset + 1}: t={frame['second']:.2f} s")
        document.setFont('Helvetica', 8)
        document.drawString(28, 18, 'Sampled frames only. Does not prove continuous motion or transcribed audio.')
        document.showPage()
    document.save()
    return target


def prepare_outputs(root: Path, data: dict) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    data['frames_opened_by_model'] = False  # Only ChatGPT viewing tool can confirm this later
    build_contact_sheet(root, data)
    build_pdf(root, data)
    manifest = dict(data)
    manifest.pop('local_video_path', None)
    (root / 'metadata.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


def render_html(data: dict, base: str, demo: bool = False) -> str:
    kind = escape(str(data.get('source_kind', 'demo')))
    label = escape(str(data.get('label', 'Public video')))
    source = escape(str(data.get('source_url', 'synthetic sample')))
    images = ''.join(
        f'<figure><img loading="lazy" src="{escape(base)}/frame/{escape(frame["filename"])}" '
        f'alt="Actual decoded video frame at {frame["second"]:.2f} seconds"/>'
        f'<figcaption>t = {frame["second"]:.2f} seconds</figcaption></figure>'
        for frame in data['frames']
    )
    notice = '<p><strong>Synthetic test source only. NOT an Instagram Reel.</strong></p>' if demo else ''
    robots = 'index,follow' if demo else 'noindex,nofollow,noarchive'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ULR - {label}</title><meta name="robots" content="{robots}"></head>
<body style="font-family:system-ui,Arial,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;background:#111827;color:#f9fafb">
<h1>Universal Link Reader Cloud PoC</h1>{notice}<p>{label} | {kind} | duration: {data['duration_seconds']:.2f} seconds</p>
<p>Source: {source}</p><p><a style="color:#7dd3fc" href="{escape(base)}/report.pdf">Timestamped PDF report</a> | <a style="color:#7dd3fc" href="{escape(base)}/contact.jpg">Contact sheet</a> | <a style="color:#7dd3fc" href="{escape(base)}/meta.json">Evidence metadata JSON</a></p>
<p>Video decoded and frames extracted by the service; <strong>this does not prove ChatGPT opened the images</strong>. No audio transcription.</p>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px">{images}</div>
<footer><small>Public test artefact. Sampled frames do not establish what happened between timestamps.</small></footer>
<style>figure{{margin:0;padding:8px;background:#1f2937;border-radius:8px}}img{{width:100%;max-height:420px;object-fit:contain}}figcaption{{text-align:center;margin-top:6px}}</style>
</body></html>'''
