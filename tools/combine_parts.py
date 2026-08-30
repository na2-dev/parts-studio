# パーツ別に塗った glb を1つにまとめる（材質はパーツごとに分けたまま）。
#
# 頭と体で別々のテクスチャを持たせるのが目的なので、統合してもアトラスは混ぜない。
# glTF は1ファイルに複数のプリミティブと材質を持てる。
import sys
import trimesh

out = trimesh.Scene()
dst = sys.argv[1]
for i, p in enumerate(sys.argv[2:]):
    m = trimesh.load(p, force='mesh', process=False)
    tex = getattr(m.visual.material, 'baseColorTexture', None)
    print(f'  {p.split("/")[-1]}: 面 {len(m.faces):,} / テクスチャ '
          f'{None if tex is None else tex.size}', flush=True)
    out.add_geometry(m, geom_name=f'part{i}')
out.export(dst)
print('保存:', dst, flush=True)
