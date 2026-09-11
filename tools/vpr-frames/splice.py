"""Вклеить блок варианта в variants.py, пересобрать JSON, дописать EXPECTED
и VIDEO_KEY в tests/test_content.py.

    py -3 splice.py block_10.py 10 '{0: "16", 2: "30"}'
"""
import re, subprocess, sys

block_file, n, key = sys.argv[1], int(sys.argv[2]), sys.argv[3]
slug = f"vpr4-2025-var-{n:02d}"
s = open("variants.py", encoding="utf-8").read()
b = open(block_file, encoding="utf-8").read()
m = "\n\ndef main():"
assert f"yashchenko({n}," not in s, "уже вклеен"
open("variants.py", "w", encoding="utf-8").write(s.replace(m, b + m, 1))

out = subprocess.run([sys.executable, "variants.py"], capture_output=True, text=True, encoding="utf-8").stdout
line = next(l for l in out.splitlines() if f'"{slug}"' in l) + "\n"

import pathlib
t = pathlib.Path(__file__).resolve().parents[2] / "tests" / "test_content.py"
ts = open(t, encoding="utf-8").read()
assert slug not in ts, "уже в тестах"
prev = f'"vpr4-2025-var-{n - 1:02d}"'
# EXPECTED: после строки предыдущего варианта
i = ts.index(f"    {prev}: [")
j = ts.index("\n", i) + 1
ts = ts[:j] + line + ts[j:]
# VIDEO_KEY: после записи предыдущего варианта (может занимать 1-2 строки)
i = ts.index(f"    {prev}: {{")
j = ts.index("},\n", i) + 3
ts = ts[:j] + f'    "{slug}": {key},\n' + ts[j:]
open(t, "w", encoding="utf-8", newline="\n").write(ts)
sys.stdout.reconfigure(encoding="utf-8")
print(line.strip())
