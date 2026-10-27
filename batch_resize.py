# -*- coding: utf-8 -*-
"""
사진 일괄 크기조절 - 폴더 배치 버전
웹 버전(사진일괄조절.html)에서 저장한 preset.json을 그대로 읽어 동일한 결과를 만듭니다.

설치:  pip install pillow
사용:  python batch_resize.py 원본폴더 결과폴더 --preset preset.json
      python batch_resize.py 원본폴더 결과폴더 --size 1000x1000 --fit contain --bg-color #ffffff
"""

import argparse
import json
import math
import os
import sys

try:
    from PIL import Image, ImageOps, ImageFilter
except ImportError:
    sys.exit("Pillow가 필요합니다.  설치:  pip install pillow")

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

DEFAULTS = {
    "width": 1200, "height": 1200,
    "fit": "contain",          # contain(전체 보이기) | cover(꽉 채우기) | long(크기만 줄이기)
    "longEdge": 1600,
    "noUpscale": False,        # False = 키워서 채우기 (웹 버전 기본값)
    "hAlign": "center",        # left | center | right
    "vAlign": "middle",        # top | middle | bottom
    "pad": 0,
    "offsetX": 0, "offsetY": 0,
    "subject": False,          # 피사체 크기 통일
    "subjectRatio": 0.8,       # 피사체가 규격에서 차지할 비율
    "bg": "auto",              # auto | color | transparent | blur
    "bgColor": "#ffffff",
    "format": "image/jpeg",    # image/jpeg | image/png | image/webp
    "quality": 0.90,
    "prefix": "", "suffix": "",
    "sequence": False,
}

FMT = {
    "image/jpeg": ("JPEG", ".jpg"),
    "image/png":  ("PNG",  ".png"),
    "image/webp": ("WEBP", ".webp"),
}

FIT_KO = {"contain": "전체 보이기", "cover": "꽉 채우기", "long": "크기만 줄이기"}

BAD_CHARS = '\\/:*?"<>|'


def hex_to_rgb(s):
    s = (s or "#ffffff").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def auto_bg_color(img):
    """사진 테두리 색을 뽑아 여백 색으로 씁니다. 웹 버전 autoBgColor()와 같은 계산."""
    n = 40
    small = img.convert("RGBA").resize((n, n), Image.LANCZOS)
    px = small.load()
    r = g = b = 0
    cnt = 0
    for y in range(n):
        for x in range(n):
            if 1 < x < n - 2 and 1 < y < n - 2:      # 바깥 테두리만
                continue
            pr, pg, pb, pa = px[x, y]
            if pa < 8:
                continue
            r += pr
            g += pg
            b += pb
            cnt += 1
    if not cnt:
        return (255, 255, 255)
    return (round(r / cnt), round(g / cnt), round(b / cnt))


def subject_box(img):
    """사진에서 실제 내용물(피사체)이 차지하는 범위를 찾습니다.
    투명 배경(PNG)이면 투명하지 않은 부분을, 단색 배경이면 배경색과 다른 부분을 봅니다.
    웹 버전 subjectBox()와 같은 계산."""
    iw, ih = img.size
    N = 220                                          # 작게 줄여서 빠르게 찾습니다
    k = min(N / iw, N / ih, 1.0)
    w = max(1, round(iw * k))
    h = max(1, round(ih * k))
    small = img.convert("RGBA").resize((w, h), Image.LANCZOS)
    px = small.load()

    has_alpha = any(px[x, y][3] < 240 for y in range(h) for x in range(w))

    br = bg = bb = 0.0
    if not has_alpha:                                # 단색 배경이라면 테두리 색을 배경으로
        edge = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
        edge += [(0, y) for y in range(1, h - 1)] + [(w - 1, y) for y in range(1, h - 1)]
        for x, y in edge:
            p = px[x, y]
            br += p[0]
            bg += p[1]
            bb += p[2]
        n = len(edge)
        br /= n
        bg /= n
        bb /= n

    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if has_alpha:
                solid = p[3] > 24
            else:
                dr, dg, db = p[0] - br, p[1] - bg, p[2] - bb
                solid = math.sqrt(dr * dr + dg * dg + db * db) > 26
            if solid:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y

    if max_x >= min_x and max_y >= min_y:
        return (min_x / k, min_y / k, (max_x - min_x + 1) / k, (max_y - min_y + 1) / k)
    return (0.0, 0.0, float(iw), float(ih))          # 못 찾으면 사진 전체


def layout(iw, ih, s, box=None):
    """웹 버전의 layout()과 동일한 계산. (cw, ch, x, y, dw, dh) 를 돌려줍니다."""
    if s["fit"] == "long":
        k = s["longEdge"] / max(iw, ih)
        if s["noUpscale"]:
            k = min(k, 1.0)
        cw, ch = max(1, round(iw * k)), max(1, round(ih * k))
        return cw, ch, 0, 0, cw, ch

    cw, ch = int(s["width"]), int(s["height"])
    pad = int(s["pad"])
    aw, ah = max(1, cw - pad * 2), max(1, ch - pad * 2)
    ax = {"left": 0.0, "center": 0.5, "right": 1.0}[s["hAlign"]]
    ay = {"top": 0.0, "middle": 0.5, "bottom": 1.0}[s["vAlign"]]

    # 피사체 크기 맞추기 — 사진 전체가 아니라 '내용물'을 기준으로 크기를 정합니다
    if s["subject"] and box and box[2] > 0 and box[3] > 0:
        bx, by, bw0, bh0 = box
        r = max(0.05, min(1.0, s["subjectRatio"]))
        k = min(aw * r / bw0, ah * r / bh0)
        if s["noUpscale"]:
            k = min(k, 1.0)
        bw, bh = bw0 * k, bh0 * k
        x = pad + (aw - bw) * ax - bx * k + s["offsetX"]
        y = pad + (ah - bh) * ay - by * k + s["offsetY"]
        return cw, ch, round(x), round(y), max(1, round(iw * k)), max(1, round(ih * k))

    k = max(aw / iw, ah / ih) if s["fit"] == "cover" else min(aw / iw, ah / ih)
    if s["noUpscale"]:
        k = min(k, 1.0)

    dw, dh = iw * k, ih * k
    x = pad + (aw - dw) * ax + s["offsetX"]
    y = pad + (ah - dh) * ay + s["offsetY"]
    return cw, ch, round(x), round(y), max(1, round(dw)), max(1, round(dh))


def render(src, s):
    img = ImageOps.exif_transpose(src)          # 휴대폰 사진 회전 정보 반영
    img = img.convert("RGBA")
    iw, ih = img.size

    box = subject_box(img) if s["subject"] and s["fit"] != "long" else None
    cw, ch, x, y, dw, dh = layout(iw, ih, s, box)

    opaque = (s["format"] == "image/jpeg")
    transparent_bg = (s["bg"] == "transparent" and not opaque)
    auto = auto_bg_color(img) if s["bg"] == "auto" else None

    if s["fit"] == "long":
        canvas = img.resize((cw, ch), Image.LANCZOS)
    else:
        if transparent_bg:
            canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        elif s["bg"] == "blur":
            # 흐림이 가장자리까지 번지므로, 흐림 반경만큼 넉넉히 키워서 모서리가 비지 않게 합니다.
            rad = max(4, round(min(cw, ch) / 22))
            m = rad * 6
            bk = max((cw + m) / iw, (ch + m) / ih) * 1.05
            bw, bh = max(1, round(iw * bk)), max(1, round(ih * bk))
            bgimg = img.resize((bw, bh), Image.LANCZOS).filter(ImageFilter.GaussianBlur(rad))
            canvas = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
            canvas.paste(bgimg, ((cw - bw) // 2, (ch - bh) // 2))
        else:
            fill = auto if auto else hex_to_rgb(
                "#ffffff" if s["bg"] == "transparent" else s["bgColor"])
            canvas = Image.new("RGBA", (cw, ch), tuple(fill) + (255,))

        scaled = img.resize((dw, dh), Image.LANCZOS)
        canvas.paste(scaled, (x, y), scaled)     # 캔버스를 넘치는 부분은 자동으로 잘림

    if opaque:
        if s["bg"] == "auto" and auto:
            flat_fill = tuple(auto)
        elif s["bg"] in ("transparent", "blur"):
            flat_fill = (255, 255, 255)
        else:
            flat_fill = hex_to_rgb(s["bgColor"])
        flat = Image.new("RGB", canvas.size, flat_fill)
        flat.paste(canvas, mask=canvas.split()[3])
        return flat
    return canvas


def out_name(stem, idx, s, ext):
    base = f"{idx + 1:03d}" if s["sequence"] else stem
    base = (s["prefix"] or "") + base + (s["suffix"] or "")
    for ch in BAD_CHARS:
        base = base.replace(ch, "_")
    return base + ext


def collect(src_dir, recursive):
    files = []
    if recursive:
        for root, _, names in os.walk(src_dir):
            for n in names:
                if os.path.splitext(n)[1].lower() in EXTS:
                    files.append(os.path.join(root, n))
    else:
        for n in sorted(os.listdir(src_dir)):
            p = os.path.join(src_dir, n)
            if os.path.isfile(p) and os.path.splitext(n)[1].lower() in EXTS:
                files.append(p)
    files.sort(key=lambda p: os.path.basename(p).lower())
    return files


def main():
    ap = argparse.ArgumentParser(description="사진 일괄 크기조절 (폴더 배치)")
    ap.add_argument("src", help="원본 폴더")
    ap.add_argument("dst", help="결과 폴더 (없으면 만듭니다)")
    ap.add_argument("--preset", help="웹 버전에서 저장한 preset.json")
    ap.add_argument("--size", help="가로x세로, 예: 1000x1000")
    ap.add_argument("--ratio", help="비율로 크기 정하기, 예: 16:9 (긴 쪽 길이는 그대로 둡니다)")
    ap.add_argument("--fit", choices=["contain", "cover", "long"], help="맞추는 방식")
    ap.add_argument("--long-edge", type=int, help="크기만 줄이기일 때 긴 변 픽셀")
    ap.add_argument("--align", help="위치, 예: center-middle / left-top / right-bottom")
    ap.add_argument("--pad", type=int, help="안쪽 여백 px")
    ap.add_argument("--subject", action="store_true", help="피사체 크기를 모두 같게 맞추기")
    ap.add_argument("--subject-ratio", type=int, help="피사체가 차지할 비율 퍼센트 (기본 80)")
    ap.add_argument("--bg", choices=["auto", "color", "transparent", "blur"], help="여백 채우는 방법")
    ap.add_argument("--bg-color", help="배경색 (#ffffff) — 색 직접 고르기")
    ap.add_argument("--format", choices=["jpg", "png", "webp"], help="저장 형식")
    ap.add_argument("--quality", type=int, help="품질 40~100")
    ap.add_argument("--prefix", help="파일명 앞에 붙일 말")
    ap.add_argument("--suffix", help="파일명 뒤에 붙일 말")
    ap.add_argument("--sequence", action="store_true", help="001,002… 로 새로 번호 매기기")
    ap.add_argument("--no-upscale", action="store_true", help="작은 사진을 키우지 않기 (원래 크기로 두기)")
    ap.add_argument("--allow-upscale", action="store_true", help="작은 사진도 키워서 채우기 (기본값)")
    ap.add_argument("--recursive", action="store_true", help="하위 폴더까지 포함")
    a = ap.parse_args()

    s = dict(DEFAULTS)
    if a.preset:
        with open(a.preset, encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})

    if a.size:
        try:
            w, h = a.size.lower().split("x")
            s["width"], s["height"] = int(w), int(h)
        except ValueError:
            sys.exit("--size 형식이 잘못되었습니다. 예: --size 1000x1000")
    if a.ratio:
        # 웹 버전 비율 버튼과 같게 — 지금 크기의 '긴 쪽'을 유지한 채 방향만 바꿉니다.
        try:
            ra, rb = (float(v) for v in a.ratio.replace("/", ":").split(":"))
            if ra <= 0 or rb <= 0:
                raise ValueError
        except ValueError:
            sys.exit("--ratio 형식이 잘못되었습니다. 예: --ratio 16:9")
        base = max(s["width"], s["height"])
        lim = lambda v: max(1, min(12000, round(v)))
        if ra >= rb:
            s["width"], s["height"] = lim(base), lim(base * rb / ra)
        else:
            s["height"], s["width"] = lim(base), lim(base * ra / rb)
    if a.fit:
        s["fit"] = a.fit
    if a.long_edge:
        s["longEdge"] = a.long_edge
    if a.align:
        try:
            hx, vy = a.align.lower().split("-")
            s["hAlign"], s["vAlign"] = hx, vy
        except ValueError:
            sys.exit("--align 형식이 잘못되었습니다. 예: --align center-middle")
        if s["hAlign"] not in ("left", "center", "right") or s["vAlign"] not in ("top", "middle", "bottom"):
            sys.exit("--align 값이 잘못되었습니다. 가로는 left|center|right, 세로는 top|middle|bottom")
    if a.pad is not None:
        s["pad"] = a.pad
    if a.subject:
        s["subject"] = True
    if a.subject_ratio:
        s["subjectRatio"] = a.subject_ratio / 100
    if a.bg:
        s["bg"] = a.bg
    if a.bg_color:
        s["bgColor"] = a.bg_color
        if not a.bg:
            s["bg"] = "color"        # 색을 직접 줬으면 자동이 아니라 그 색으로
    if a.format:
        s["format"] = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[a.format]
    if a.quality:
        s["quality"] = a.quality / 100
    if a.prefix is not None:
        s["prefix"] = a.prefix
    if a.suffix is not None:
        s["suffix"] = a.suffix
    if a.sequence:
        s["sequence"] = True
    if a.no_upscale:
        s["noUpscale"] = True
    if a.allow_upscale:
        s["noUpscale"] = False

    if not os.path.isdir(a.src):
        sys.exit(f"원본 폴더를 찾을 수 없습니다: {a.src}")
    os.makedirs(a.dst, exist_ok=True)

    files = collect(a.src, a.recursive)
    if not files:
        sys.exit("처리할 이미지가 없습니다.")

    pil_fmt, ext = FMT[s["format"]]
    spec = f'{s["width"]}x{s["height"]}' if s["fit"] != "long" else f'긴 변 {s["longEdge"]}'
    extra = f' / 피사체 {round(s["subjectRatio"] * 100)}%' if s["subject"] else ""
    print(f'사진 {len(files)}장  →  {spec} / {FIT_KO[s["fit"]]} / '
          f'{s["hAlign"]}-{s["vAlign"]} / 여백 {s["bg"]}{extra} / {pil_fmt}')

    used, ok, bad = set(), 0, 0
    for i, path in enumerate(files):
        stem = os.path.splitext(os.path.basename(path))[0]
        name = out_name(stem, i, s, ext)
        n = 2
        while name.lower() in used:
            name = out_name(stem, i, s, "") + f"({n}){ext}"
            n += 1
        used.add(name.lower())

        try:
            with Image.open(path) as im:
                out = render(im, s)
                kw = {}
                if pil_fmt in ("JPEG", "WEBP"):
                    kw["quality"] = int(s["quality"] * 100)
                if pil_fmt == "JPEG":
                    kw["subsampling"] = 0
                    kw["optimize"] = True
                out.save(os.path.join(a.dst, name), pil_fmt, **kw)
            ok += 1
            print(f"  [{i + 1}/{len(files)}] {os.path.basename(path)} → {name}")
        except Exception as e:
            bad += 1
            print(f"  [{i + 1}/{len(files)}] 실패: {os.path.basename(path)} ({e})")

    print(f"\n완료 — 성공 {ok}장" + (f", 실패 {bad}장" if bad else "") + f"\n결과 폴더: {os.path.abspath(a.dst)}")


if __name__ == "__main__":
    main()
