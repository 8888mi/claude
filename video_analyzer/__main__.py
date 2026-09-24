"""CLI: python -m video_analyzer <видео|URL> [...] [--out DIR] [--json]"""

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

from .analyzer import analyze_video


def _fetch(src: str, tmp: Path) -> str:
    if not src.startswith(("http://", "https://")):
        return src
    name = src.split("?")[0].rstrip("/").split("/")[-1] or "video"
    if "." not in name:
        name += ".mp4"
    dst = tmp / name
    urllib.request.urlretrieve(src, dst)
    return str(dst)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Проверка видео на критичные артефакты по ТЗ")
    ap.add_argument("videos", nargs="+", help="пути к файлам или прямые ссылки")
    ap.add_argument("--out", default="out", help="папка для раскадровки и JSON-отчётов")
    ap.add_argument("--json", action="store_true", help="печатать полный JSON-отчёт")
    a = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="va_"))
    rc = 0
    for src in a.videos:
        try:
            rep = analyze_video(_fetch(src, tmp), a.out)
        except Exception as e:  # noqa: BLE001
            print(f"[ОШИБКА] {src}: {e}", file=sys.stderr)
            rc = 1
            continue
        if a.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
            continue
        m = rep["meta"]
        print(f"\n=== {src}")
        print(f"    {m['width']}x{m['height']}, {m['fps']} fps, {m['duration_s']} c")
        print(f"    ВЕРДИКТ: {rep['verdict']}")
        print(f"    {rep['action']}")
        for f in rep["findings"]:
            mark = "❌" if f["critical"] else "ℹ️ "
            print(f"    {mark} [{f['confidence']}] {f['title']}: {f['details']}")
        if rep.get("contact_sheet"):
            print(f"    раскадровка: {rep['contact_sheet']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
