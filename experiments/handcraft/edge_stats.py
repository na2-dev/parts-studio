# 元絵の輪郭（半透明の画素）の色を調べる。
# ★仮説: 透過 PNG の縁の画素は RGB が白っぽく、投影でそれを拾うと
#   「手の白い筋」「兜の縁の白い斑」になり、埋めでさらに広がる。
# 使いかた: python edge_stats.py 絵dir
import os, sys
import numpy as np
from PIL import Image

d = sys.argv[1]
for n in ['front', 'left', 'back', 'right']:
    a = np.asarray(Image.open(os.path.join(d, f'{n}.png')).convert('RGBA')).astype(float)
    al = a[..., 3]
    bands = [('0<a<128', (al > 0) & (al < 128)), ('128<=a<250', (al >= 128) & (al < 250)), ('a>=250', al >= 250)]
    print(n, a.shape[:2])
    for label, m in bands:
        if m.sum():
            print(f'   {label:12s} n={int(m.sum()):8d} meanRGB={a[m][:, :3].mean(0).round().astype(int)}')
    # 不透明の内側で、輪郭から 1〜3px の画素の色（縁の線が白いか黒いか）
    from scipy import ndimage
    solid = al >= 128
    dist = ndimage.distance_transform_edt(solid)
    rim = solid & (dist <= 3)
    inner = solid & (dist > 3) & (dist <= 8)
    print(f'   rim(<=3px)   meanRGB={a[rim][:, :3].mean(0).round().astype(int)}  inner(3-8px) meanRGB={a[inner][:, :3].mean(0).round().astype(int)}')
