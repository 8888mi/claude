"""Синтетические видео с известными артефактами: python -m pytest tests -q"""

import cv2
import numpy as np
import pytest

from video_analyzer import analyze_video

W, H, FPS = 640, 360, 25


def _scene(t, rng_tex):
    # «сложная» движущаяся сцена: текстура с панорамой + движущийся объект
    x = int(t * 40) % 200
    fr = rng_tex[:, x:x + W].copy()
    cx = int(100 + 60 * t) % W
    cv2.circle(fr, (cx, 180), 40, (30, 60, 220), -1)
    return fr


def _texture():
    rng = np.random.default_rng(0)
    base = rng.integers(0, 255, (H // 8, (W + 200) // 8, 3), dtype=np.uint8)
    tex = cv2.resize(base, (W + 200, H), interpolation=cv2.INTER_CUBIC)
    return cv2.GaussianBlur(tex, (3, 3), 0)


def make(path, dur=4.0, mod=None):
    tex = _texture()
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(int(dur * FPS)):
        t = i / FPS
        fr = _scene(t, tex)
        if mod:
            fr = mod(fr, t)
        vw.write(fr)
    vw.release()
    return str(path)


def codes(rep):
    return {f["code"] for f in rep["findings"] if f["critical"]}


def bars(fr, t):
    fr[:, :70] = 0
    fr[:, -70:] = 0
    return fr


def overexp(fr, t):
    fr[: int(H * 0.45)] = 255
    return fr


def blur_mid(fr, t):
    return cv2.GaussianBlur(fr, (0, 0), 12) if 1.0 <= t < 2.6 else fr


def watermark(fr, t):
    cv2.putText(fr, "LOGO TV", (470, 340), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return fr


def flat(fr, t):
    fr[:, : int(W * 0.65)] = (180, 170, 160)
    return fr


def blocky(fr, t):
    small = cv2.resize(fr, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (W, H), interpolation=cv2.INTER_NEAREST)


@pytest.mark.parametrize("name,mod,dur,expect", [
    ("clean", None, 4.0, set()),
    ("short", None, 1.5, {"short"}),
    ("bars", bars, 4.0, {"borders"}),
    ("overexp", overexp, 4.0, {"overexposed"}),
    ("blur", blur_mid, 4.0, {"full_blur"}),
    ("watermark", watermark, 4.0, {"overlay"}),
    ("flat", flat, 4.0, {"low_complexity"}),
    ("blocky", blocky, 4.0, {"blockiness"}),
])
def test_artifacts(tmp_path, name, mod, dur, expect):
    rep = analyze_video(make(tmp_path / f"{name}.mp4", dur, mod), tmp_path / "out")
    got = codes(rep)
    if expect:
        assert expect <= got, (got, rep["metrics"])
        assert rep["verdict"] == "BAD"
    else:
        assert got == set(), (got, rep["metrics"])
        assert rep["verdict"] == "OK_PENDING_VISUAL"
