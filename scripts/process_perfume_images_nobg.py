#!/usr/bin/env python3
"""Batch-generate nobg thumbnails, upload to Storage, update perfumes.image_url_nobg."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from utils.db import get_all_perfumes, update_perfume_image_nobg
from utils.image_nobg import save_nobg_thumbnail


async def main() -> int:
    limit = int(os.getenv("IMAGE_NOBG_BATCH_LIMIT", "500"))
    offset = int(os.getenv("IMAGE_NOBG_BATCH_OFFSET", "0"))
    perfumes = await get_all_perfumes(limit=limit, offset=offset)
    if not perfumes:
        print("No perfumes found.")
        return 0

    saved = 0
    uploaded = 0
    for row in perfumes:
        result = save_nobg_thumbnail(row)
        if not result:
            continue
        saved += 1
        nobg_url = row.get("image_url_nobg")
        fid = row.get("fragrantica_id")
        if nobg_url and fid is not None:
            if await update_perfume_image_nobg(
                fragrantica_id=int(fid),
                image_url_nobg=str(nobg_url),
            ):
                uploaded += 1

    print(f"Done: {saved}/{len(perfumes)} processed, {uploaded} DB rows updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
