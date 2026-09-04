# パーツ別に塗った glb を1つにまとめる（材質はパーツごとに分けたまま）。
#
# 頭と体で別々のテクスチャを持たせるのが目的なので、統合してもアトラスは混ぜない。
# glTF は1ファイルに複数のプリミティブと材質を持てる。
#
# ★1つの Trimesh に結合してはいけない。テクスチャが1枚に混ざり、
#   パーツごとに 2048 を使い切るという ADR-0008 の狙いが消える。
#
# 使いかた: python tools\\combine_parts.py 出力.glb パーツ1.glb パーツ2.glb ...
import os
import sys

import trimesh


# ★glTF は【Y が上】と決まっている（仕様 3.5「the +Y axis is up」）。
#   このパイプラインの中身は Z 上（TRELLIS.2 の向きをそのまま持ち回っている）ので、
#   最後にここで直す。直さないと、出来た glb は標準のビューアで
#   【寝たまま】表示される（2026-08-31 に実測して気づいた）。
#   途中のファイルは Z 上のままにしてある。工程どうしの受け渡しは内部の話なので、
#   規約に合わせるのは人が開く最後の1つだけでよい。
UP_TO_GLTF = {
    # Z上 -> Y上。(x, y, z) -> (x, z, -y)
    'z': ((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)),
    'y': None,                                   # 既に glTF の向き
}


def combine(dst, parts, up='z'):
    """パーツを1つの glb にまとめる。材質はパーツごとに分けたまま。

    up: 渡すパーツの上方向。'z' なら glTF の Y 上へ直して書き出す。
    """
    if not parts:
        raise SystemExit('まとめるパーツがありません')
    if up not in UP_TO_GLTF:
        raise SystemExit(f"上方向は {sorted(UP_TO_GLTF)} のいずれか。受け取った値: {up!r}")
    import numpy as np
    m = UP_TO_GLTF[up]
    rot = None if m is None else np.array(m, dtype=np.float64)
    if rot is not None:
        print('  glTF の向き（Y上）に直します', flush=True)
    scene = trimesh.Scene()
    info = []
    for i, path in enumerate(parts):
        if not os.path.isfile(path):
            raise SystemExit(f'パーツが見つかりません: {path}')
        m = trimesh.load(path, force='mesh', process=False)
        if rot is not None:
            # ★頂点に焼き込む。Scene.apply_transform だと書き出しで落ちる（実測）。
            #   trimesh は glb へ頂点をそのまま書き、ノード変換を持たせないため
            m.apply_transform(rot)
        tex = getattr(getattr(m.visual, 'material', None), 'baseColorTexture', None)
        size = None if tex is None else tex.size
        print(f'  {os.path.basename(path)}: 面 {len(m.faces):,} / テクスチャ {size}',
              flush=True)
        scene.add_geometry(m, geom_name=f'part{i}')
        info.append({'path': path, 'faces': len(m.faces), 'texture': size})
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    scene.export(dst)
    print('保存:', dst, flush=True)
    return info


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    up = 'z'
    for a in sys.argv[1:]:
        if a.startswith('--up='):
            up = a.split('=', 1)[1]
    if len(args) < 2:
        sys.exit('使いかた: combine_parts.py 出力.glb パーツ1.glb パーツ2.glb ... [--up=z|y]')
    combine(args[0], args[1:], up)
