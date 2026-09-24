"""Автоматические проверки видеофрагмента по ТЗ (проекты 86-87, 88-89).

Какие критичные артефакты ловятся автоматически (эвристики):
  * короткое видео (< 2 c);
  * рамки / поля / чёрные полосы по краям, в т.ч. размытая «подложка» по бокам;
  * пересвет (> 20 % кадра выбито в белый);
  * полный блюр / расфокус всего кадра дольше 1 c;
  * низкая комплексность (простой или размытый фон > 50 % кадра);
  * блочность / пикселизация сжатия;
  * статичные наложения (вотермарки, логотипы, плашки) — по неподвижным контурам
    на движущейся сцене.

Что автоматически НЕ решается и остаётся на визуальную проверку по раскадровке:
  отзеркаливание (по тексту), субтитры и сменяющийся наложенный текст, эффект
  старой плёнки, нейросетевая генерация, непонятный сюжет, размытый передний план.

Некритичные особенности (склейки, статика, темнота, мерцание) тоже измеряются,
но только как справка — по ТЗ они видео не бракуют.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cv2
import numpy as np


@dataclass
class AnalysisConfig:
    sample_fps: float = 8.0            # сколько кадров в секунду анализировать
    work_width: int = 640              # ширина рабочей копии кадра
    min_duration_s: float = 2.0        # ТЗ: видео короче 2 c — битое
    border_min_frac: float = 0.015     # полоса толще 1.5 % стороны кадра — рамка
    border_row_std: float = 7.0        # «однотонность» строки/столбца полосы
    overexp_level: int = 245           # пиксель считается выбитым в белый
    overexp_frac: float = 0.20         # ТЗ: > 20 % кадра
    overexp_min_share: float = 0.25    # доля кадров с пересветом для вердикта
    blur_cell_thr: float = 18.0        # резкость самой резкой ячейки кадра ниже — кадр размыт
    blur_min_run_s: float = 1.0        # ТЗ: блюр дольше 1 c
    flat_cell_grad: float = 3.5        # средний градиент ячейки ниже — ячейка «пустая»
    flat_frac: float = 0.50            # ТЗ: простой/размытый фон > 50 % кадра
    blockiness_thr: float = 1.35       # отношение перепадов на границах 8x8 к внутренним
    overlay_persist: float = 0.85      # контур присутствует в >= 85 % кадров
    overlay_min_area_frac: float = 0.0008
    scene_cut_thr: float = 0.45        # 1 - корреляция гистограмм между соседними кадрами
    dark_mean: float = 40.0


@dataclass
class Finding:
    code: str
    title: str                 # формулировка из таблицы критериев ТЗ
    critical: bool
    confidence: str            # high / medium / low
    details: dict = field(default_factory=dict)


# Названия артефактов — как в сводной таблице ТЗ.
TITLES = {
    "short": "Видео короче 2-х секунд",
    "borders": "Рамочки, поля, черные полосы по краям кадра",
    "blurred_fill": "Рамочки/поля: размытая подложка по краям кадра",
    "overexposed": "Пересвеченное видео (более 20% кадра засвечено до белого)",
    "full_blur": "Полный блюр или расфокус всего кадра более 1 секунды",
    "low_complexity": "Низкая комплексность (простой/сильно размытый фон > 50% кадра)",
    "blockiness": "Технические дефекты (крупная пикселизация, блочность)",
    "overlay": "Вотермарки, логотипы, наложенный текст (статичное наложение)",
    # некритичные — справочно
    "cuts": "Монтажные склейки (некритично)",
    "static": "Низкая динамика / статика (некритично)",
    "dark": "Очень темное видео (некритично, если видно происходящее)",
    "flicker": "Мерцание яркости (некритично; при царапинах/зерне — проверить эффект плёнки)",
    "low_fps": "Недостаточный FPS (некритично)",
}

VISUAL_CHECKLIST = [
    "Вотермарки, логотипы, дата, плашки, субтитры, любой наложенный текст (даже уместный по сюжету)",
    "Отзеркаливание — перевёрнутые буквы/цифры на вывесках, номерах, одежде",
    "Эффект старой плёнки: искусственные царапины, пыль, выцветание, звуковая дорожка сбоку кадра",
    "Нестандартная геометрия — кадр «обрублен», повёрнут, вставлен в фигуру",
    "Размытый передний план — абстрактные пятна > 20 % кадра, заслоняющие главный объект",
    "Сюжет понятен и описывается словами",
    "Признаки генерации нейросетью (плывущие руки/лица/текст, морфинг объектов)",
    "Рамка: при сомнении сверить края кадра на тёмном фоне (мини-проигрыватель)",
]


# ---------------------------------------------------------------- чтение видео

def _probe(cap: cv2.VideoCapture) -> dict:
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    return {"fps": fps, "frame_count": n, "width": w, "height": h}


def read_frames(path: str, sample_fps: float):
    """Возвращает (метаданные, [(t, BGR-кадр в исходном разрешении)])."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео: {path}")
    meta = _probe(cap)
    fps = meta["fps"] if meta["fps"] > 0 else 25.0
    step = max(1, int(round(fps / sample_fps)))
    frames, idx = [], 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, fr = cap.retrieve()
            if ok and fr is not None:
                frames.append((idx / fps, fr))
        idx += 1
    cap.release()
    meta["decoded_frames"] = idx
    meta["duration_s"] = idx / fps if idx else 0.0
    return meta, frames


def _resize(fr: np.ndarray, width: int) -> np.ndarray:
    h, w = fr.shape[:2]
    if w == width:
        return fr
    return cv2.resize(fr, (width, int(round(h * width / w))), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- проверки

def _edge_bar(img: np.ndarray, axis: int, from_start: bool, row_std: float) -> int:
    """Толщина однотонной полосы у края (в пикселях)."""
    n = img.shape[axis]
    limit = n // 3
    first_mean = None
    for k in range(limit):
        i = k if from_start else n - 1 - k
        line = img[i, :, :] if axis == 0 else img[:, i, :]
        std = float(line.reshape(-1, 3).std(axis=0).max())
        mean = line.reshape(-1, 3).mean(axis=0)
        if std > row_std:
            return k
        if first_mean is None:
            first_mean = mean
        elif np.abs(mean - first_mean).max() > 12:   # полоса должна быть одного цвета
            return k
    # однотонная зона до трети кадра — это содержимое сцены (небо, стена), а не рамка
    return 0


def check_borders(frames, cfg: AnalysisConfig):
    sides = {"top": (0, True), "bottom": (0, False), "left": (1, True), "right": (1, False)}
    thick = {s: [] for s in sides}
    for _, fr in frames:
        small = _resize(fr, cfg.work_width).astype(np.float32)
        for s, (ax, st) in sides.items():
            thick[s].append(_edge_bar(small, ax, st, cfg.border_row_std))
    h, w = _resize(frames[0][1], cfg.work_width).shape[:2]
    res = {}
    for s, vals in thick.items():
        vals = np.array(vals)
        dim = h if s in ("top", "bottom") else w
        med = float(np.median(vals))
        present = float((vals >= cfg.border_min_frac * dim).mean())
        # рамка стабильна во времени: присутствует почти во всех кадрах
        res[s] = {"median_px": med, "frac_of_side": med / dim, "present_share": present}
    bad = {s: v for s, v in res.items() if v["present_share"] >= 0.9}
    return res, bad


def check_blurred_fill(frames, cfg: AnalysisConfig, borders_bad: dict):
    """Вертикальное видео, вставленное в горизонтальный кадр с размытой копией по бокам."""
    ratios = []
    for _, fr in frames:
        g = cv2.cvtColor(_resize(fr, cfg.work_width), cv2.COLOR_BGR2GRAY)
        w = g.shape[1]
        s = int(w * 0.15)
        lap = np.abs(cv2.Laplacian(g, cv2.CV_32F))
        side = (lap[:, :s].mean() + lap[:, -s:].mean()) / 2
        center = lap[:, int(w * 0.35):int(w * 0.65)].mean()
        ratios.append(side / (center + 1e-6))
    r = float(np.median(ratios))
    return r, (r < 0.2 and not borders_bad)


def _cell_grid(g: np.ndarray, n: int):
    h, w = g.shape
    ys = np.linspace(0, h, n + 1, dtype=int)
    xs = np.linspace(0, w, n + 1, dtype=int)
    for i in range(n):
        for j in range(n):
            yield g[ys[i]:ys[i + 1], xs[j]:xs[j + 1]]


def frame_metrics(fr: np.ndarray, cfg: AnalysisConfig, crop=None) -> dict:
    small = _resize(fr, cfg.work_width)
    if crop:
        t, b, l, r = crop
        small = small[t:small.shape[0] - b or None, l:small.shape[1] - r or None]
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    # пересвет: все каналы близки к 255
    over = float((small.min(axis=2) >= cfg.overexp_level).mean())
    # резкость: максимум по ячейкам 8x8 дисперсии лапласиана — художественное
    # размытие фона не считается блюром, пока хоть что-то в кадре резкое
    lap = cv2.Laplacian(g, cv2.CV_32F)
    cell_sharp = [float(c.var()) for c in _cell_grid(lap, 8)]
    sharp = float(np.percentile(cell_sharp, 95))
    # комплексность: доля «пустых» ячеек 16x16 по среднему градиенту
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy) / 4.0
    flat = float(np.mean([c.mean() < cfg.flat_cell_grad for c in _cell_grid(mag, 16)]))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    return {"overexp": over, "sharp": sharp, "flat": flat,
            "mean": float(g.mean()), "sat": float(hsv[..., 1].mean())}


def blockiness(fr: np.ndarray) -> float:
    """Отношение перепадов на границах блоков 8x8 к перепадам внутри блоков (исходное разрешение)."""
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # обрезаем крупные перепады: настоящие контуры сцены, случайно попавшие
    # на границу блока, не должны выглядеть как блочность
    dx = np.minimum(np.abs(np.diff(g, axis=1)), 20)
    dy = np.minimum(np.abs(np.diff(g, axis=0)), 20)
    bx = dx[:, 7::8].mean()
    ix = np.delete(dx, np.s_[7::8], axis=1).mean()
    by = dy[7::8, :].mean()
    iy = np.delete(dy, np.s_[7::8], axis=0).mean()
    return float(((bx + by) / 2) / ((ix + iy) / 2 + 1e-6))


def check_overlay(frames, cfg: AnalysisConfig, crop):
    """Неподвижные контуры на движущейся сцене — кандидаты во вотермарки/логотипы/плашки."""
    grays = []
    for _, fr in frames:
        small = _resize(fr, cfg.work_width)
        t, b, l, r = crop
        small = small[t:small.shape[0] - b or None, l:small.shape[1] - r or None]
        grays.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    if len(grays) < 6:
        return None
    stack = np.stack(grays).astype(np.float32)
    edges = np.stack([cv2.Canny(g, 60, 150) > 0 for g in grays])
    persist = edges.mean(axis=0)
    tstd = stack.std(axis=0)
    scene_motion = float(np.median(tstd))
    # если вся сцена неподвижна (штатив, статика) — метод не различает наложение и сцену
    if scene_motion < 4.0:
        return {"scene_motion": scene_motion, "reliable": False, "regions": []}
    mask = ((persist >= cfg.overlay_persist) & (tstd < 12)).astype(np.uint8)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    H, W = mask.shape
    regions = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < cfg.overlay_min_area_frac * H * W:
            continue
        # сплошные огромные области — скорее неподвижный объект сцены
        if w * h > 0.25 * H * W:
            continue
        cx, cy = (x + w / 2) / W, (y + h / 2) / H
        pos = ("верх" if cy < 0.33 else "низ" if cy > 0.67 else "центр") + "-" + \
              ("лево" if cx < 0.33 else "право" if cx > 0.67 else "центр")
        regions.append({"box_xywh_rel": [round(float(v), 3) for v in (x / W, y / H, w / W, h / H)],
                        "position": pos, "area_px": int(a)})
    return {"scene_motion": round(scene_motion, 2), "reliable": True, "regions": regions}


def scene_cuts(frames, cfg: AnalysisConfig):
    cuts, prev = [], None
    for t, fr in frames:
        hsv = cv2.cvtColor(_resize(fr, 320), cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(h, h)
        if prev is not None:
            d = 1 - cv2.compareHist(prev, h, cv2.HISTCMP_CORREL)
            if d > cfg.scene_cut_thr:
                cuts.append(round(t, 2))
        prev = h
    return cuts


def _runs(flags, times):
    """Непрерывные отрезки True -> [(t0, t1)]."""
    out, start = [], None
    for f, t in zip(flags, times):
        if f and start is None:
            start = t
        if not f and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, times[-1] + (times[1] - times[0] if len(times) > 1 else 0)))
    return out


# ---------------------------------------------------------------- раскадровка

def contact_sheet(frames, out_path: Path, n: int = 12, cols: int = 4, tile_w: int = 480):
    if not frames:
        return None
    idx = np.linspace(0, len(frames) - 1, min(n, len(frames))).round().astype(int)
    tiles = []
    for i in idx:
        t, fr = frames[i]
        tile = _resize(fr, tile_w).copy()
        # светлая обводка, чтобы чёрные рамки видео были видны на тёмном фоне листа
        cv2.rectangle(tile, (0, 0), (tile.shape[1] - 1, tile.shape[0] - 1), (0, 200, 255), 1)
        cv2.putText(tile, f"{t:.2f}s", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
        cv2.putText(tile, f"{t:.2f}s", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        tiles.append(tile)
    th = max(t.shape[0] for t in tiles)
    rows = math.ceil(len(tiles) / cols)
    pad = 8
    sheet = np.full((rows * (th + pad) + pad, cols * (tile_w + pad) + pad, 3), 40, np.uint8)
    for k, tile in enumerate(tiles):
        r, c = divmod(k, cols)
        y, x = pad + r * (th + pad), pad + c * (tile_w + pad)
        sheet[y:y + tile.shape[0], x:x + tile.shape[1]] = tile
    cv2.imwrite(str(out_path), sheet)
    return str(out_path)


# ---------------------------------------------------------------- главный вход

def analyze_video(path: str, out_dir: str | None = None, cfg: AnalysisConfig | None = None) -> dict:
    cfg = cfg or AnalysisConfig()
    path = str(path)
    meta, frames = read_frames(path, cfg.sample_fps)
    findings: list[Finding] = []
    if not frames:
        raise RuntimeError("В видео нет декодируемых кадров")

    if meta["duration_s"] < cfg.min_duration_s:
        findings.append(Finding("short", TITLES["short"], True, "high",
                                {"duration_s": round(meta["duration_s"], 2)}))

    # рамки
    border_stats, border_bad = check_borders(frames, cfg)
    if border_bad:
        findings.append(Finding("borders", TITLES["borders"], True, "high",
                                {s: round(v["frac_of_side"] * 100, 1) for s, v in border_bad.items()}))
    fill_ratio, fill_bad = check_blurred_fill(frames, cfg, border_bad)
    if fill_bad:
        findings.append(Finding("blurred_fill", TITLES["blurred_fill"], True, "medium",
                                {"side_to_center_sharpness": round(fill_ratio, 3)}))

    # дальше анализируем кадр без рамок, чтобы полосы не портили метрики
    ws = cfg.work_width
    hs = _resize(frames[0][1], ws).shape[0]
    crop = tuple(int(border_stats[s]["median_px"]) if s in border_bad else 0
                 for s in ("top", "bottom", "left", "right"))
    if crop[0] + crop[1] >= hs - 10 or crop[2] + crop[3] >= ws - 10:
        crop = (0, 0, 0, 0)

    times = [t for t, _ in frames]
    fm = [frame_metrics(fr, cfg, crop) for _, fr in frames]

    # пересвет
    over = np.array([m["overexp"] for m in fm])
    over_share = float((over > cfg.overexp_frac).mean())
    if over_share >= cfg.overexp_min_share:
        findings.append(Finding("overexposed", TITLES["overexposed"], True,
                                "high" if over_share > 0.6 else "medium",
                                {"max_white_frac": round(float(over.max()), 3),
                                 "share_of_frames": round(over_share, 2)}))

    # полный блюр > 1 c
    sharp = np.array([m["sharp"] for m in fm])
    blur_runs = [r for r in _runs(list(sharp < cfg.blur_cell_thr), times)
                 if r[1] - r[0] > cfg.blur_min_run_s]
    if blur_runs:
        findings.append(Finding("full_blur", TITLES["full_blur"], True, "medium",
                                {"intervals_s": [[round(a, 2), round(b, 2)] for a, b in blur_runs]}))

    # низкая комплексность
    flat = np.array([m["flat"] for m in fm])
    mean_l = np.array([m["mean"] for m in fm])
    flat_med = float(np.median(flat))
    if flat_med > cfg.flat_frac:
        dark = float(np.median(mean_l)) < cfg.dark_mean
        findings.append(Finding("low_complexity", TITLES["low_complexity"], True,
                                "low" if dark else "medium",
                                {"flat_area_frac": round(flat_med, 2),
                                 "note": "кадр тёмный — пустые зоны могут быть тенью" if dark else ""}))

    # блочность (на исходном разрешении, без ресайза)
    blk_idx = np.linspace(0, len(frames) - 1, min(10, len(frames))).round().astype(int)
    blk = float(np.median([blockiness(frames[i][1]) for i in blk_idx]))
    if blk > cfg.blockiness_thr:
        findings.append(Finding("blockiness", TITLES["blockiness"], True,
                                "high" if blk > 1.7 else "medium", {"blockiness": round(blk, 2)}))

    # статичные наложения
    ov = check_overlay(frames, cfg, crop)
    if ov and ov["reliable"] and ov["regions"]:
        findings.append(Finding("overlay", TITLES["overlay"], True, "medium", ov))

    # ---- некритичное, справочно
    cuts = scene_cuts(frames, cfg)
    if cuts:
        findings.append(Finding("cuts", TITLES["cuts"], False, "high", {"at_s": cuts}))
    grays = [cv2.cvtColor(_resize(fr, 320), cv2.COLOR_BGR2GRAY).astype(np.float32) for _, fr in frames]
    motion = float(np.median([np.abs(a - b).mean() for a, b in zip(grays, grays[1:])])) if len(grays) > 1 else 0.0
    if motion < 1.0:
        findings.append(Finding("static", TITLES["static"], False, "medium", {"mean_abs_diff": round(motion, 2)}))
    if float(np.median(mean_l)) < cfg.dark_mean:
        findings.append(Finding("dark", TITLES["dark"], False, "high", {"mean_luma": round(float(np.median(mean_l)), 1)}))
    if len(mean_l) > 4:
        flick = float(np.median(np.abs(np.diff(mean_l))))
        if flick > 4.0:
            findings.append(Finding("flicker", TITLES["flicker"], False, "low",
                                    {"median_luma_jump": round(flick, 2),
                                     "mean_saturation": round(float(np.median([m['sat'] for m in fm])), 1)}))
    if 0 < meta["fps"] < 20:
        findings.append(Finding("low_fps", TITLES["low_fps"], False, "high", {"fps": round(meta["fps"], 2)}))

    critical = [f for f in findings if f.critical]
    confident = [f for f in critical if f.confidence in ("high", "medium")]
    if confident:
        verdict = "BAD"
        action = "Плохое кадрирование/артефакт → отметить: " + "; ".join(f.title for f in confident)
    else:
        verdict = "OK_PENDING_VISUAL"
        action = ("Автопроверки чисто. Пройти визуальный чек-лист по раскадровке; "
                  "если критичного нет → «Отправить» (в спорных случаях завышать оценку).")

    report = {
        "file": path,
        "meta": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in meta.items()},
        "verdict": verdict,
        "action": action,
        "findings": [asdict(f) for f in findings],
        "metrics": {
            "border_stats": border_stats,
            "blurred_fill_ratio": round(fill_ratio, 3),
            "overexp_max": round(float(over.max()), 3),
            "sharpness_min": round(float(sharp.min()), 1),
            "sharpness_median": round(float(np.median(sharp)), 1),
            "flat_area_median": round(flat_med, 3),
            "blockiness": round(blk, 3),
            "motion": round(motion, 2),
            "overlay_scene_motion": ov["scene_motion"] if ov else None,
        },
        "visual_checklist": VISUAL_CHECKLIST,
    }

    if out_dir:
        od = Path(out_dir)
        od.mkdir(parents=True, exist_ok=True)
        stem = Path(path).stem
        report["contact_sheet"] = contact_sheet(frames, od / f"{stem}_sheet.jpg")
        # отдельные кадры крупно — для поиска отзеркаленного текста и мелких логотипов
        big = []
        for k, i in enumerate(np.linspace(0, len(frames) - 1, min(3, len(frames))).round().astype(int)):
            p = od / f"{stem}_frame{k}.jpg"
            cv2.imwrite(str(p), _resize(frames[i][1], 1280))
            big.append(str(p))
        report["frames"] = big
        (od / f"{stem}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
