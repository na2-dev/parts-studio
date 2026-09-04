# UV を xatlas で切り直す（venv）。Smart UV Project は島が細かく砕けて継ぎ目が無数にできた。
# 使いかた: venv\Scripts\python.exe uv_xatlas.py 入力.glb 出力.glb
import sys, numpy as np, trimesh, xatlas
src, dst = sys.argv[1], sys.argv[2]
m = trimesh.load(src, force='mesh', process=False)
V = np.asarray(m.vertices, np.float32); F = np.asarray(m.faces, np.uint32)
atlas = xatlas.Atlas(); atlas.add_mesh(V, F)
co = xatlas.ChartOptions(); co.max_iterations = 4
po = xatlas.PackOptions(); po.resolution = 2048; po.padding = 4; po.bruteForce = False
atlas.generate(chart_options=co, pack_options=po)
vmap, idx, uv = atlas[0]
print(f'xatlas: 島 {atlas.chart_count} / 頂点 {len(V):,} → {len(vmap):,}（継ぎ目で複製）/ 利用率 {atlas.utilization:.1%}', flush=True)
out = trimesh.Trimesh(vertices=V[vmap], faces=idx.astype(np.int64), process=False,
                      visual=trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial()))
out.export(dst); print('保存:', dst, flush=True)
