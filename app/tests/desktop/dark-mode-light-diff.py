#!/usr/bin/env python3
"""Light-theme regression check for the Nuova dark-mode work.

Compares same-named PNGs in BASELINE and CANDIDATE directories (Electron
captures of the light Nuova / Classica) and reports pixels that changed.
The modern header band (top 58 px) is excluded only for '-modern-' captures,
because the Light/Dark selector intentionally narrows the header search box.

Usage: python3 dark-mode-light-diff.py BASELINE_DIR CANDIDATE_DIR [glob]
Exit 1 when any compared image differs outside the excluded band.
"""
import fnmatch
import json
import os
import sys

from PIL import Image, ImageChops

HEADER_BAND = 58


def compare(a_path, b_path, skip_header):
    a = Image.open(a_path).convert('RGB')
    b = Image.open(b_path).convert('RGB')
    if a.size != b.size:
        return {'sizeMismatch': [a.size, b.size]}
    if skip_header:
        w, h = a.size  # Electron captures are at content size (DPR 1)
        a = a.crop((0, HEADER_BAND, w, h))
        b = b.crop((0, HEADER_BAND, w, h))
    diff = ImageChops.difference(a, b).convert('L').point(lambda v: 255 if v > 8 else 0)
    box = diff.getbbox()
    changed = diff.histogram()[255] if box else 0
    return {'changedPixels': changed, 'bbox': box}


def main():
    base, cand = sys.argv[1], sys.argv[2]
    pattern = sys.argv[3] if len(sys.argv) > 3 else '*.png'
    report, failed = {}, False
    for name in sorted(os.listdir(cand)):
        if not fnmatch.fnmatch(name, pattern) or not name.endswith('.png'):
            continue
        ref = os.path.join(base, name)
        if not os.path.exists(ref):
            report[name] = 'no baseline'
            continue
        result = compare(ref, os.path.join(cand, name), '-modern-' in name)
        report[name] = result
        if result.get('sizeMismatch') or result.get('changedPixels'):
            failed = True
    print(json.dumps(report, indent=1))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
