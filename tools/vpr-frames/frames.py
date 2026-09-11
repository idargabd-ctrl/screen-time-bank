"""Кадры из ролика: раз в N секунд, без почти одинаковых соседей, сеткой 2x3."""
import subprocess, sys, os, glob, shutil
from PIL import Image, ImageChops

src, out, step = sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "10"
shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"fps=1/{step},scale=854:-1",
                f"{out}/f%03d.jpg"], check=True)
files = sorted(glob.glob(f"{out}/f*.jpg"))
# Из серии похожих кадров оставляем ПОСЛЕДНИЙ: на нём уже дописано решение.
kept, prev = [], None
for f in files:
    im = Image.open(f).convert("L").resize((107, 60))
    if prev is not None:
        diff = ImageChops.difference(im, prev)
        mean = sum(diff.getdata()) / (107 * 60)
        if mean < 6:
            os.remove(kept[-1]); kept[-1] = f; prev = im; continue
    kept.append(f); prev = im
print(f"кадров {len(files)}, осталось {len(kept)}")
# сетки по 6
grids = []
for i in range(0, len(kept), 6):
    chunk = kept[i:i + 6]
    ims = [Image.open(f) for f in chunk]
    w, h = ims[0].size
    grid = Image.new("RGB", (w * 2, h * ((len(chunk) + 1) // 2)), "white")
    for j, im in enumerate(ims):
        grid.paste(im, ((j % 2) * w, (j // 2) * h))
    name = f"{out}/grid{i // 6 + 1}.jpg"
    grid.save(name, quality=85); grids.append(name)
print("\n".join(grids))
