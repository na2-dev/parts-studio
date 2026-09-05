# UV の実面積とラスタ被覆率を測る（venv）。島が占める割合が xatlas の利用率と食い違うときの切り分け
import sys, numpy as np, trimesh, torch
import nvdiffrast.torch as dr
for p in sys.argv[1:]:
    m = trimesh.load(p, force='mesh', process=False)
    UV = np.asarray(m.visual.uv, np.float32); F = np.asarray(m.faces, np.int32)
    a = UV[F[:, 0]]; b = UV[F[:, 1]]; c = UV[F[:, 2]]
    area = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    dev = torch.device('cuda'); ctx = dr.RasterizeCudaContext()
    UVt = torch.tensor(UV, device=dev); Ft = torch.tensor(F, device=dev)
    clip = torch.cat([UVt * 2 - 1, torch.zeros_like(UVt[:, :1]), torch.ones_like(UVt[:, :1])], 1)[None]
    covs = {}
    for res in (2048, 4096, 8192):
        rast, _ = dr.rasterize(ctx, clip, Ft, (res, res))
        covs[res] = float((rast[0, ..., 3] > 0).float().mean())
    tiny = float((area * 2048 * 2048 < 1.0).mean())
    print(f'{p}: 面 {len(F):,} UV範囲 [{UV.min():.3f},{UV.max():.3f}] UV面積合計 {area.sum():.3f} 1テクセル未満の面 {tiny:.1%} 被覆 2048:{covs[2048]:.1%} 4096:{covs[4096]:.1%} 8192:{covs[8192]:.1%}', flush=True)
