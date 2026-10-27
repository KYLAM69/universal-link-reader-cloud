#!/usr/bin/env python3
"""Build-only public Reel proof. Failure publishes diagnostics; no raw MP4 in image."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.media import instagram_media_source, fetch_mp4, process_mp4
from app.reporting import prepare_outputs
from app.security import RejectedURL, canonical_instagram

SHORTCODE="Db5BK0xCh6b"
SOURCE=f"https://www.instagram.com/reel/{SHORTCODE}/"
OUTPUT=ROOT/"static"/"reel-proof"/SHORTCODE
MAX_DOWNLOAD=40*1024*1024
MAX_PDF_BYTES=4_000_000

def public_error(exc):
    result={"error_type":type(exc).__name__}
    if isinstance(exc,HTTPError):
        result.update(http_status=exc.code,error_code="http_error")
    elif isinstance(exc,URLError):
        result.update(transport_error=type(exc.reason).__name__,error_code="network_error")
    elif isinstance(exc,RejectedURL):
        result["error_code"]="source_rejected"
    elif isinstance(exc,(subprocess.CalledProcessError,subprocess.TimeoutExpired)):
        result["error_code"]="media_processing_error"
    elif isinstance(exc,RuntimeError):
        allowed={"no_exact_post_declared_public_mp4","unexpected_instagram_content_type",
                 "instagram_page_too_large","video_exceeds_max_bytes",
                 "duration_invalid_or_too_long","missing_video_stream","no_decoded_frames",
                 "unsupported_video_dimensions","not_an_mp4_container"}
        code=str(exc).split(":",1)[0]
        result["error_code"]=code if code in allowed else "processing_error"
    else:
        result["error_code"]="unexpected_error"
    return result

def build(output=OUTPUT,source=SOURCE):
    canonical,code=canonical_instagram(source)
    if code!=SHORTCODE or canonical!=SOURCE:
        raise ValueError("only_fixed_public_test_reel_allowed")
    shutil.rmtree(output,ignore_errors=True)
    output.mkdir(parents=True,exist_ok=True)
    result={"status":"unavailable","shortcode":SHORTCODE,"source_url":canonical,
            "created_at_utc":datetime.now(timezone.utc).isoformat(),
            "video_verified":False,"frames_extracted":0,
            "frames_opened_by_model":False,"audio_transcribed":False}
    stage="public_page"
    try:
        media_url,details=instagram_media_source(canonical)
        if not details.get("exact_post_matched") or details.get("shortcode")!=SHORTCODE:
            raise RuntimeError("source_identity_not_verified")
        stage="media_download"
        movie=output/"input.mp4"
        size=fetch_mp4(media_url,movie,"meta",MAX_DOWNLOAD)
        stage="video_probe_and_frames"
        data=process_mp4(movie,output,"Public Instagram Reel frame sample",frame_count=6,max_duration=120)
        data.update(status="frames_ready",source_kind="instagram",source_url=canonical,
                    exact_post_matched=True,shortcode=SHORTCODE,downloaded_bytes=size,
                    source_sha256=hashlib.sha256(movie.read_bytes()).hexdigest())
        stage="report_creation"
        manifest=prepare_outputs(output,data)
        if not (0<(output/"report.pdf").stat().st_size<=MAX_PDF_BYTES):
            raise RuntimeError("pdf_exceeds_response_limit")
        result.update(status="frames_ready",video_verified=True,
                      frames_extracted=len(manifest["frames"]),
                      duration_seconds=manifest["duration_seconds"],
                      pdf_bytes=(output/"report.pdf").stat().st_size,
                      exact_post_matched=True)
    except Exception as exc:
        result.update(status="unavailable",failed_stage=stage,**public_error(exc))
        for f in output.iterdir():
            if f.name!="status.json" and f.is_file():
                f.unlink(missing_ok=True)
    finally:
        (output/"input.mp4").unlink(missing_ok=True)
    (output/"status.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    return result

if __name__=="__main__":
    report=build()
    print("FIXED_REEL_PROOF_BUILD status=%s stage=%s frames=%s" %
          (report["status"],report.get("failed_stage","complete"),report["frames_extracted"]))
