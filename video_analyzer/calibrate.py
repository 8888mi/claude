"""Скачивает видеопримеры из ТЗ и сверяет вердикт анализатора с ожидаемым.

python -m video_analyzer.calibrate [--dir examples] [--out out/examples]
Уже скачанные файлы (examples/<id>.*) повторно не качаются — можно положить их вручную.
"""

import argparse
import json
import urllib.request
from pathlib import Path


EXAMPLES = json.loads((Path(__file__).with_name("tz_examples.json")).read_text())


def download(ex, d: Path):
    have = sorted(d.glob(f"{ex['id']}.*"))
    if have:
        return have[0]
    ext = ".mkv" if ".mkv" in ex["url"] else ".mp4"
    dst = d / f"{ex['id']}{ext}"
    urllib.request.urlretrieve(ex["url"], dst)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--out", default="out/examples")
    ap.add_argument("--download-only", action="store_true",
                    help="только скачать примеры (без анализа и без OpenCV)")
    a = ap.parse_args()
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    if a.download_only:
        for ex in EXAMPLES:
            try:
                print(f"[ok] {download(ex, d)}")
            except Exception as e:  # noqa: BLE001
                print(f"[--] {ex['id']}: {e}")
        return
    from .analyzer import analyze_video  # OpenCV нужен только для анализа

    hit = hit_final = total = 0
    for ex in EXAMPLES:
        try:
            p = download(ex, d)
            rep = analyze_video(str(p), a.out)
        except Exception as e:  # noqa: BLE001
            print(f"[--] {ex['id']:16} не скачан/не открыт: {e}")
            continue
        got = "BAD" if rep["verdict"] == "BAD" else "OK"
        final = ex.get("final", ex["expected"])
        total += 1
        hit += got == ex["expected"]
        hit_final += got == final
        found = ", ".join(f"{f['code']}({f['confidence']})" for f in rep["findings"] if f["critical"]) or "-"
        # [~~] — расхождение с меткой ТЗ объясняется другими дефектами клипа (см. note)
        mark = "OK" if got == ex["expected"] else "~~" if got == final else "XX"
        print(f"[{mark}] {ex['id']:16} ТЗ {ex['expected']:3} итог {final:3} авто {got:3} | {found} | {ex['artifact']}")
    print(f"\nАвто = метка ТЗ: {hit}/{total}; авто = итог с визуальной проверкой: {hit_final}/{total}. "
          "Промахи на тексте/плёнке/переднем плане/пикселизации — визуальная часть проверки.")


if __name__ == "__main__":
    main()
