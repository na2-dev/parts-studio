# UV を xatlas で切り直す（venv）。Smart UV Project は島が細かく砕けて継ぎ目が無数にできた。
# 使いかた: venv\Scripts\python.exe uv_xatlas.py 入力.glb 出力.glb
import sys, numpy as np, trimesh, xatlas
src, dst = sys.argv[1], sys.argv[2]
m = trimesh.load(src, force='mesh', process=False)
V = np.asarray(m.vertices, np.float32); F = np.asarray(m.faces, np.uint32)
atlas = xatlas.Atlas(); atlas.add_mesh(V, F)
co = xatlas.ChartOptions(); co.max_iterations = int(__import__("os").environ.get("XA_ITERS", 4))   # ★40万三角形では 4 だと1時間超えて終わらなかった（2026-09-05）。大きなメッシュは 1
# ★余白は 1px に。削減メッシュは島が 2 万個になり、余白 4px が面積の 7 割を食って UV 面積が 29% に落ちた（2026-09-05）。
#   島を減らすため max_cost を上げる（既定 2 → 8）。解像度は 4096（1 テクセル未満の三角形を減らす）
import os
co.max_cost = float(os.environ.get('XA_MAXCOST', 8)); co.normal_deviation_weight = float(os.environ.get('XA_NDEV', 1.0))
po = xatlas.PackOptions(); po.resolution = int(os.environ.get('XA_RES', 4096)); po.padding = int(os.environ.get('XA_PAD', 1)); po.bruteForce = False
atlas.generate(chart_options=co, pack_options=po)
vmap, idx, uv = atlas[0]
print(f'xatlas: 島 {atlas.chart_count} / 頂点 {len(V):,} → {len(vmap):,}（継ぎ目で複製）/ 利用率 {atlas.utilization:.1%}', flush=True)
out = trimesh.Trimesh(vertices=V[vmap], faces=idx.astype(np.int64), process=False,
                      visual=trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial()))
out.export(dst); print('保存:', dst, flush=True)
