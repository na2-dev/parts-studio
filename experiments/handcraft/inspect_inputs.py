# 入力の4枚を確かめる: 大きさ・透過・被写体の bbox・高さの一致。
import sys, os
import numpy as np
from PIL import Image
d = sys.argv[1]
rows = []
for v in ('front', 'left', 'right', 'back'):
    im = Image.open(os.path.join(d, f'{v}.png'))
    a = np.asarray(im.convert('RGBA'))
    alpha = a[..., 3]
    ys, xs = np.where(alpha > 8)
    h, w = ys.max() - ys.min(), xs.max() - xs.min()
    # 半透明のふちがどれくらいあるか（背景ぬきの質）
    soft = ((alpha > 8) & (alpha < 247)).sum() / max(1, (alpha > 8).sum())
    rows.append((v, im.size, im.mode, (xs.min(), ys.min(), xs.max(), ys.max()), h, w, soft))
    print(f'{v:5s} {im.size} {im.mode} bbox=({xs.min()},{ys.min()})-({xs.max()},{ys.max()}) '
          f'高さ {h} 幅 {w} 半透明ふち {soft:.1%}')
hs = [r[4] for r in rows]
print(f'高さの差: 最大 {max(hs)} / 最小 {min(hs)} → {(max(hs)-min(hs))/max(hs):.1%}')
tops = [r[3][1] for r in rows]; bots = [r[3][3] for r in rows]
print(f'上端のずれ {max(tops)-min(tops)}px / 下端のずれ {max(bots)-min(bots)}px')
