"""Склейка страниц по ключевым словам: py -3 strip.py v11 перевес точки Казани болт [--last]
Берётся ПОСЛЕДНИЙ кадр с этим словом (там дописано решение); --first — первый."""
import glob, sys
from PIL import Image

v, words = sys.argv[1], [w for w in sys.argv[2:] if not w.startswith("--")]
first = "--first" in sys.argv
fs = sorted(glob.glob(f"frames/{v}/f*.jpg"))
chunks = open(f"frames/{v}/ocr.txt", encoding="utf-8").read().split("\n=====\n")


def page(i):
    im = Image.open(fs[i]); w, h = im.size
    return im.crop((30, 0, int(w * 0.72), h)).resize((700, int(700 * h / (w * 0.72 - 30))), Image.LANCZOS)


picks = []
for wd in words:
    hits = [i for i, x in enumerate(chunks) if wd in x]
    if hits:
        picks.append(hits[0] if first else hits[-1])
picks = list(dict.fromkeys(picks))
pages = [page(i) for i in picks]
g = Image.new("RGB", (700 * len(pages), max(p.height for p in pages)), "white")
for j, p in enumerate(pages):
    g.paste(p, (700 * j, 0))
g.save(f"frames/{v}/strip.jpg", quality=80)
print(picks, g.size)
