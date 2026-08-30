# テクスチャ付きメッシュから、指定の高さより上/下だけを取り出す（UVと材質を保つ）。
#
# ★なぜ要るか（2026-08-30）
#   体パーツに元絵を投影しようとしたが、apply_detail が絵とメッシュをそれぞれ
#   自分の高さで再正規化するため、パーツ単体では対応が取れず 1画素も貼れなかった
#   （シルエットの一致 0.29〜0.48）。
#   そこで【全身で塗って投影したもの】から体だけを取り出す。全身での投影は
#   実証済み（貼れた画素 46.53%）なので、確実な経路だけで組める。
import sys
import numpy as np
import trimesh


def extract(src, dst, keep, cut_frac=None, cut_abs=None):
    m = trimesh.load(src, force='mesh', process=False)
    v = np.asarray(m.vertices, dtype=np.float64)
    f = np.asarray(m.faces)
    ext = v.max(0) - v.min(0)
    up = int(np.argmax(ext))
    h = v[:, up]
    if cut_abs is not None:
        cut = cut_abs
    else:
        cut = h.min() + (h.max() - h.min()) * (cut_frac if cut_frac is not None else 0.5)
    fh = h[f].mean(axis=1)
    sel = fh > cut if keep == 'upper' else fh <= cut
    uniq, inv = np.unique(f[sel].reshape(-1), return_inverse=True)
    uv = np.asarray(m.visual.uv)[uniq]
    sub = trimesh.Trimesh(vertices=v[uniq], faces=inv.reshape(-1, 3), process=False,
                          visual=trimesh.visual.TextureVisuals(uv=uv, material=m.visual.material))
    sub.export(dst)
    print(f'{keep}: 面 {int(sel.sum()):,} / 頂点 {len(uniq):,} '
          f'(上方向は軸{up} / 切る高さ {cut:.4f}) -> {dst}', flush=True)
    return cut


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit('使いかた: extract_part.py 入力.glb 出力.glb upper|lower [--frac=0.5|--abs=z]')
    frac, ab = None, None
    for a in sys.argv[4:]:
        if a.startswith('--frac='): frac = float(a.split('=', 1)[1])
        if a.startswith('--abs='):  ab = float(a.split('=', 1)[1])
    extract(sys.argv[1], sys.argv[2], sys.argv[3], frac, ab)
