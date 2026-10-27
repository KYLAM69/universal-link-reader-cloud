#!/usr/bin/env python3
"""Create real synthetic test video -> decoded frames -> PDF, no remote network."""
from pathlib import Path
import sys
import subprocess
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.media import process_mp4
from app.reporting import prepare_outputs


def main():
    root = ROOT / 'static' / 'demo'
    root.mkdir(parents=True, exist_ok=True)
    movie = root / 'synthetic.mp4'
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi',
        '-i', 'testsrc2=size=320x240:rate=8', '-t', '4', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-y', str(movie)
    ], check=True, timeout=30)
    data = process_mp4(movie, root, 'Synthetic moving test pattern', frame_count=4, max_duration=10)
    data.update(source_kind='synthetic', source_url='generated_on_server',
                status='frames_ready', exact_post_matched=False)
    prepare_outputs(root, data)
    movie.unlink(missing_ok=True)
    print(f"DEMO_BUILT frames={len(data['frames'])} pdf_bytes={(root / 'report.pdf').stat().st_size}")


if __name__ == '__main__':
    main()
