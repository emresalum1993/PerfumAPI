"""Download perfume thumbnails, save locally, upload to Supabase Storage."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_OUTPUT_DIR = "data/images/nobg"
DEFAULT_BUCKET = "perfume-thumbs"
_rembg_session = None
_bucket_ready = False


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_output_dir() -> Path:
    raw = (os.getenv("IMAGE_NOBG_DIR") or DEFAULT_OUTPUT_DIR).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = get_project_root() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def nobg_output_path(perfume_data: Dict[str, Any]) -> Path:
    out_dir = get_output_dir()
    fragrantica_id = perfume_data.get("fragrantica_id")
    if fragrantica_id is not None:
        return out_dir / f"{int(fragrantica_id)}.png"
    image_url = (perfume_data.get("image_url") or "").strip()
    digest = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:12]
    return out_dir / f"url_{digest}.png"


def _get_rembg_session():
    global _rembg_session
    if _rembg_session is None:
        from rembg import new_session

        model = (os.getenv("IMAGE_NOBG_MODEL") or "u2net").strip()
        _rembg_session = new_session(model)
    return _rembg_session


def remove_background(image_bytes: bytes) -> bytes:
    from rembg import remove
    from PIL import Image

    session = _get_rembg_session()
    result = remove(image_bytes, session=session)
    img = Image.open(io.BytesIO(result)).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def download_image(url: str, timeout: int = 30) -> bytes:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; perfume-api/1.0)"},
    )
    response.raise_for_status()
    return response.content


def storage_object_path(perfume_data: Dict[str, Any]) -> Optional[str]:
    fragrantica_id = perfume_data.get("fragrantica_id")
    if fragrantica_id is None:
        return None
    return f"{int(fragrantica_id)}.png"


def get_storage_bucket() -> str:
    return (os.getenv("IMAGE_NOBG_BUCKET") or DEFAULT_BUCKET).strip()


def _upload_enabled() -> bool:
    return _env_truthy("IMAGE_NOBG_UPLOAD", default=True)


def ensure_nobg_bucket() -> None:
    global _bucket_ready
    if _bucket_ready or not _upload_enabled():
        return
    from utils.db import supabase

    bucket = get_storage_bucket()
    try:
        supabase.storage.get_bucket(bucket)
    except Exception:
        supabase.storage.create_bucket(bucket, options={"public": True})
    _bucket_ready = True


def upload_nobg_png(perfume_data: Dict[str, Any], png_bytes: bytes) -> Optional[str]:
    """Upload PNG to Supabase Storage; return public URL."""
    if not _upload_enabled():
        return None
    object_path = storage_object_path(perfume_data)
    if not object_path:
        return None
    ensure_nobg_bucket()
    from utils.db import supabase

    bucket = get_storage_bucket()
    supabase.storage.from_(bucket).upload(
        object_path,
        png_bytes,
        file_options={"content-type": "image/png", "upsert": "true"},
    )
    return supabase.storage.from_(bucket).get_public_url(object_path)


def upload_nobg_from_path(perfume_data: Dict[str, Any], local_path: Path) -> Optional[str]:
    if not local_path.is_file():
        return None
    return upload_nobg_png(perfume_data, local_path.read_bytes())


def save_nobg_thumbnail(
    perfume_data: Dict[str, Any],
    *,
    force: bool = False,
) -> Optional[str]:
    """
    Download image_url thumbnail, remove background, save PNG locally,
    optionally upload to Supabase Storage.

    Returns public storage URL when upload succeeds, else local path, else None.
    Sets perfume_data['image_url_nobg'] when upload succeeds.
    """
    if not _env_truthy("IMAGE_NOBG", default=True):
        return None

    image_url = (perfume_data.get("image_url") or "").strip()
    if not image_url:
        return None

    out_path = nobg_output_path(perfume_data)
    skip_processing = (
        out_path.exists()
        and not force
        and not _env_truthy("IMAGE_NOBG_FORCE", default=False)
    )

    try:
        if skip_processing:
            public_url = upload_nobg_from_path(perfume_data, out_path)
            if public_url:
                perfume_data["image_url_nobg"] = public_url
                print(
                    f"☁️  Uploaded existing nobg: {out_path.name} "
                    f"({perfume_data.get('name') or 'unknown'})",
                    flush=True,
                )
                return public_url
            return str(out_path)

        raw = download_image(image_url)
        nobg = remove_background(raw)
        out_path.write_bytes(nobg)
        print(
            f"🖼️  Saved nobg thumbnail: {out_path.name} "
            f"({perfume_data.get('name') or 'unknown'})",
            flush=True,
        )

        public_url = upload_nobg_png(perfume_data, nobg)
        if public_url:
            perfume_data["image_url_nobg"] = public_url
            print(f"☁️  Stored nobg URL: {public_url}", flush=True)
            return public_url
        return str(out_path)
    except Exception as exc:
        print(
            f"⚠️  Nobg thumbnail failed for {perfume_data.get('name') or image_url}: {exc}",
            flush=True,
        )
        return None


if __name__ == "__main__":
    # ponytail: minimal self-check — solid PNG in, alpha out
    from PIL import Image, ImageDraw

    buf = io.BytesIO()
    img = Image.new("RGB", (64, 64), color=(200, 120, 80))
    draw = ImageDraw.Draw(img)
    draw.ellipse((12, 8, 52, 56), fill=(240, 200, 160))
    img.save(buf, format="PNG")
    out = remove_background(buf.getvalue())
    assert Image.open(io.BytesIO(out)).mode == "RGBA"
    print("image_nobg self-check ok")
