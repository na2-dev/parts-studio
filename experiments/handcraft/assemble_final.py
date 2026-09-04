# 最終組み立て（venv）: 形（xatlas UV）＋アルベド（元絵の投影）＋法線・AO（ハイ→ローのベイク）
# を1つの glb にし、glTF の向き（Y上・正面+Z）へ直して書く。
# 使いかた: venv\Scripts\python.exe assemble_final.py メッシュ.glb albedo.png normal.png ao.png 出力.glb
import sys, numpy as np, trimesh
from PIL import Image
from trimesh.visual.material import PBRMaterial
mesh_p, alb_p, nrm_p, ao_p, out_p = sys.argv[1:6]
m = trimesh.load(mesh_p, force='mesh', process=False)
V = np.asarray(m.vertices, np.float64); F = np.asarray(m.faces); UV = np.asarray(m.visual.uv)
alb = Image.open(alb_p).convert('RGB')
ao = Image.open(ao_p).convert('L') if ao_p != 'none' else Image.new('L', alb.size, 255)
# AO は albedo に軽く乗せる（glTF の occlusion は読み手によって無視されるため、見た目を確実にする）
a = np.asarray(alb).astype(np.float32); o = np.asarray(ao.resize(alb.size)).astype(np.float32) / 255.0
strength = float(sys.argv[6]) if len(sys.argv) > 6 else 0.35
a = a * (1 - strength + strength * o[..., None])
alb_ao = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
mat = PBRMaterial(baseColorTexture=alb_ao, metallicFactor=0.0, roughnessFactor=0.85)
if nrm_p != 'none':                                   # 'none' なら法線マップ無し
    mat.normalTexture = Image.open(nrm_p).convert('RGB')
# 内部規約（Z上・正面-Y）→ glTF（Y上・正面+Z）: (x,y,z) -> (x, z, -y)
Vy = np.stack([V[:, 0], V[:, 2], -V[:, 1]], 1)
out = trimesh.Trimesh(vertices=Vy, faces=F, process=False,
                      visual=trimesh.visual.TextureVisuals(uv=UV, material=mat))
out.export(out_p)
print(f'保存: {out_p} / 面 {len(F):,} / albedo {alb.size} / normal / AO 強さ {strength}', flush=True)
