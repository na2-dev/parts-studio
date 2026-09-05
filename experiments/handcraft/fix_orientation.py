# 面の裏表を揃える（venv）。
# ★生成直後の形は巻きが揃っておらず（外向き 46%）、Blender の再計算でも 63%（非多様体のため）。
# ★「面ごとに最寄りの参照面と比べて反転」（v1）は、帽子の縁や髪のような薄い所で反対側の参照面を
#   拾って裏返り、貼れない面が増えた（埋め 40%→64%、2026-09-05 実測）。
#   v2: 1. 辺を共有する隣の面と巻きが一致するように伝播（trimesh.repair.fix_winding）
#       2. つながった部品ごとに、参照メッシュ（ボクセルリメッシュ後・向きが揃っている）と多数決して
#          部品全体を反転するか決める。薄い所の 1 面が間違えても部品の多数決で正しくなる
# 使いかた: venv\Scripts\python.exe fix_orientation.py 入力.glb 参照.glb 出力.glb
import sys, numpy as np, trimesh
from scipy.spatial import cKDTree
src, ref_p, dst = sys.argv[1], sys.argv[2], sys.argv[3]
m = trimesh.load(src, force='mesh', process=False)
ref = trimesh.load(ref_p, force='mesh', process=False)
uv = np.asarray(m.visual.uv) if hasattr(m.visual, 'uv') and m.visual.uv is not None else None
work = trimesh.Trimesh(vertices=np.asarray(m.vertices), faces=np.asarray(m.faces).copy(), process=False)
trimesh.repair.fix_winding(work)          # 隣接面と巻きを揃える（部品の内側では一致）
F = np.asarray(work.faces).copy()
tree = cKDTree(ref.triangles_center)
_, idx = tree.query(work.triangles_center, k=1, workers=-1)
agree = np.einsum('ij,ij->i', np.asarray(work.face_normals), np.asarray(ref.face_normals)[idx])
comps = trimesh.graph.connected_components(work.face_adjacency, nodes=np.arange(len(F)), min_len=1)
n_flip_comp = 0; n_flip_face = 0
for c in comps:
    if np.median(agree[c]) < 0:      # 部品の多数が参照と逆 → 部品ごと反転
        F[c] = F[c][:, ::-1]; n_flip_comp += 1; n_flip_face += len(c)
out = trimesh.Trimesh(vertices=np.asarray(m.vertices), faces=F, process=False,
                      visual=trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial()) if uv is not None else None)
fn = np.asarray(out.face_normals)
final = (np.einsum('ij,ij->i', fn, np.asarray(ref.face_normals)[idx]) > 0).mean()
print(f'部品 {len(comps):,} / 反転した部品 {n_flip_comp:,}（{n_flip_face:,} 面）→ 参照と一致 {final:.1%}', flush=True)
out.export(dst); print('保存:', dst, flush=True)
