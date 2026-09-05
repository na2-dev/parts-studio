# 外から見えない部品（内側の殻・破片）を落とす（venv・nvdiffrast）。
# ★生成直後の形は 6.4 万個の部品に分かれ、その大半は内側の殻や破片で外からは見えない。
#   残したまま削減・UV 展開すると、xatlas が破片ごとに余白を取って UV 面積が 20% に落ちた（2026-09-05）。
#   多方向から描画して 1 面も見えない部品を捨てる。表面はそのまま（リメッシュしないので細部が残る）。
# 使いかた: venv\Scripts\python.exe drop_hidden.py 入力.glb 出力.glb [方向数=42] [解像度=2048]
import sys, math, numpy as np, trimesh, torch
import nvdiffrast.torch as dr
src, dst = sys.argv[1], sys.argv[2]
ndir = int(sys.argv[3]) if len(sys.argv) > 3 else 42
res = int(sys.argv[4]) if len(sys.argv) > 4 else 2048
m = trimesh.load(src, force='mesh', process=False)
V = np.asarray(m.vertices, np.float32); F = np.asarray(m.faces, np.int32)
ctr = (V.max(0) + V.min(0)) / 2; h = (V.max(0) - V.min(0)).max()
Vn = (V - ctr) / h                      # 半径 0.5 程度に正規化
dev = torch.device('cuda'); ctx = dr.RasterizeCudaContext()
Vt = torch.tensor(Vn, device=dev); Ft = torch.tensor(F, device=dev)
# フィボナッチ球で方向を撒く
i = np.arange(ndir) + 0.5
phi = np.arccos(1 - 2 * i / ndir); theta = math.pi * (1 + 5 ** 0.5) * i
dirs = np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], 1).astype(np.float32)
seen = torch.zeros(len(F) + 1, dtype=torch.bool, device=dev)
for d in dirs:
    d = d / np.linalg.norm(d)
    up = np.array([0, 0, 1.0], np.float32) if abs(d[2]) < 0.9 else np.array([1.0, 0, 0], np.float32)
    x = np.cross(up, d); x /= np.linalg.norm(x); y = np.cross(d, x)
    R = torch.tensor(np.stack([x, y, d], 0), device=dev)       # 行: 右, 上, 奥
    P = Vt @ R.T
    clip = torch.stack([P[:, 0] * 1.8, P[:, 1] * 1.8, P[:, 2] * 0.5, torch.ones_like(P[:, 0])], 1)[None]
    rast, _ = dr.rasterize(ctx, clip, Ft, (res, res))
    ids = rast[0, ..., 3].long().reshape(-1)
    seen[ids] = True
seen = seen[1:].cpu().numpy()             # id は 1 始まり
import os
if os.environ.get('DROP_MODE', 'face') == 'face':
    # ★面単位で落とす。部品単位だと、首や袖の隙間から数面だけ見える「内側の殻」（生成メッシュは
    #   厚み 0.04 ほどの二重の殻）が丸ごと残り、UV の半分を食い、可視判定でも手前の面と競合した
    #   （2026-09-05: 隠れ判定で落ちた面の奥行き差の中央値 0.036）。
    #   見えた面から RING 段だけ隣接面を足して、へこみの縁に小穴が開かないようにする
    ring = int(os.environ.get('DROP_RING', 3))
    keep = seen.copy()
    adj = m.face_adjacency
    for _ in range(ring):
        grow = keep[adj[:, 0]] | keep[adj[:, 1]]
        keep[adj[grow, 0]] = True; keep[adj[grow, 1]] = True
    kept = int(keep.sum())
    print(f'面単位: 見えた面 {int(seen.sum()):,} → 隣接 {ring} 段を足して {kept:,} 面を残す', flush=True)
else:
    comps = trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(F)), min_len=1)
    keep = np.zeros(len(F), bool); kept = 0
    for c in comps:
        if seen[c].any():
            keep[c] = True; kept += 1
    print(f'部品 {len(comps):,} → 見える部品 {kept:,}', flush=True)
out = trimesh.Trimesh(vertices=V, faces=F[keep], process=False)
out.remove_unreferenced_vertices()
print(f'面 {len(F):,} → {int(keep.sum()):,}', flush=True)
out.export(dst); print('保存:', dst, flush=True)
