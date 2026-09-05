# 面の裏表を数える: 重心から外向きの面の割合と、Y上/Z上の判定（venv）
import sys, numpy as np, trimesh
for p in sys.argv[1:]:
    m = trimesh.load(p, force='mesh', process=False)
    V = np.asarray(m.vertices); F = np.asarray(m.faces)
    ext = V.max(0) - V.min(0)
    c = F.shape[0]
    fn = np.asarray(m.face_normals); cen = V[F].mean(1) - V.mean(0)
    outward = (np.einsum('ij,ij->i', fn, cen) > 0).mean()
    print(f'{p}: 面 {c:,} 外向き {outward:.1%} 大きさ xyz={np.round(ext,3)} 水密={m.is_watertight} 巻き={m.is_winding_consistent}')
