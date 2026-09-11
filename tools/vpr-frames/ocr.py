"""OCR кадров варианта: текст без дублей + одна картинка-склейка нужных страниц.

    py -3 ocr.py v10            распознать (или взять кэш) и собрать zoom.jpg
    py -3 ocr.py v10 --redo     распознать заново
"""
import glob, subprocess, os, re, sys
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
T = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
os.environ["TESSDATA_PREFIX"] = os.path.abspath("tessdata")
SEP = "\n=====\n"
v = sys.argv[1]
fs = sorted(glob.glob(f"frames/{v}/f*.jpg"))
cache = f"frames/{v}/ocr.txt"

chunks = []
if os.path.exists(cache) and "--redo" not in sys.argv:
    chunks = open(cache, encoding="utf-8").read().split(SEP)
else:
    for f in fs:
        im = Image.open(f); w, h = im.size
        page = im.crop((30, 0, int(w * 0.72), h)).resize((int(w * 0.72 * 2.2), int(h * 2.2)), Image.LANCZOS).convert("L")
        page.save("ocr_tmp.png")
        r = subprocess.run([T, "ocr_tmp.png", "stdout", "-l", "rus", "--psm", "4"], capture_output=True)
        chunks.append(r.stdout.decode("utf-8", "replace"))
    open(cache, "w", encoding="utf-8").write(SEP.join(chunks))

seen, lines = set(), []
for ln in "\n".join(chunks).splitlines():
    s = re.sub(r"\s+", " ", ln).strip()
    if len(s) < 6 or s in seen or not re.search(r"[а-яА-Я]{3}", s):
        continue
    seen.add(s); lines.append(s)
if "--quiet" not in sys.argv:
    print("\n".join(lines))


def page(i):
    im = Image.open(fs[i]); w, h = im.size
    return im.crop((30, 0, int(w * 0.72), h)).resize((760, int(760 * h / (w * 0.72 - 30))), Image.LANCZOS)


def last_with(*words):
    hits = [i for i, x in enumerate(chunks) if any(wd in x for wd in words)]
    return hits[-1] if hits else None


picks = [last_with("мушкетёра", "медведя", "толстяка", "поросёнка", "капитана"),
         last_with("вчера", "завтра"), last_with("Мальчики", "Девочки"), last_with("карандаш")]
picks = list(dict.fromkeys(i for i in picks if i is not None))
if picks:
    pages = [page(i) for i in picks]
    g = Image.new("RGB", (760 * len(pages), max(p.height for p in pages)), "white")
    for j, p in enumerate(pages):
        g.paste(p, (760 * j, 0))
    g.save(f"frames/{v}/zoom.jpg", quality=80)
    print("ZOOM:", picks, g.size)
