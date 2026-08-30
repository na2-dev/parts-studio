# リトポロジー済みメッシュ＋自前UVに、ボクセル場のPBR属性を焼き込む。
#
# ★なぜ自前で書くのか（2026-08-30）
#   o_voxel.postprocess.to_glb は「出力トポロジーを内部で作る」前提のAPIで、
#   外からリトポロジー済みメッシュを渡すと焼き込みが破綻する（アトラスがほぼ真っ黒）。
#   理由は、テクセルの3D位置を BVH で【引数に渡したメッシュ】へ射影してから
#   ボクセル場を引く作りのため。渡すメッシュがボクセル表面から外れていると全部外す。
#
#   そこで役割を分ける。
#     ・BVHと射影の相手 = 元のメッシュ（ボクセル表面に乗っている）
#     ・出力トポロジーとUV = こちらで用意したもの
#   焼き込みの手順自体は to_glb と同じ（UV空間でラスタライズ → 位置を補正 → 三線形補間）。
import numpy as np
import torch
import cv2
import nvdiffrast.torch as dr
import cumesh
from flex_gemm.ops.grid_sample import grid_sample_3d


def bake(src_vertices, src_faces, attr_volume, coords, voxel_size, attr_layout,
         dst_vertices, dst_faces, dst_uvs, texture_size=2048, resolution=1024,
         aabb=((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5))):
    """
    src_*  : ボクセル表面に乗った元のメッシュ（射影の相手）
    dst_*  : 焼き込み先のメッシュとUV
    返り値 : {'base_color','metallic','roughness','alpha','mask'} の画像
    """
    aabb0 = torch.tensor(aabb[0], dtype=torch.float32, device='cuda')
    bvh = cumesh.cuBVH(src_vertices.cuda(), src_faces.cuda())

    uvs = dst_uvs.cuda().float()
    v = dst_vertices.cuda().float()
    f = dst_faces.cuda().int()

    ctx = dr.RasterizeCudaContext()
    uvs_rast = torch.cat([uvs * 2 - 1, torch.zeros_like(uvs[:, :1]),
                          torch.ones_like(uvs[:, :1])], dim=-1).unsqueeze(0)
    rast = torch.zeros((1, texture_size, texture_size, 4), device='cuda', dtype=torch.float32)
    for i in range(0, f.shape[0], 100000):
        chunk, _ = dr.rasterize(ctx, uvs_rast, f[i:i+100000],
                                resolution=[texture_size, texture_size])
        m = chunk[..., 3:4] > 0
        chunk[..., 3:4] += i
        rast = torch.where(m, chunk, rast)
    mask = rast[0, ..., 3] > 0

    pos = dr.interpolate(v.unsqueeze(0), rast, f)[0][0]
    valid = pos[mask]
    # ★ここが要点：テクセルの位置を元メッシュの表面へ射影し直す
    _, face_id, uvw = bvh.unsigned_distance(valid, return_uvw=True)
    tri = src_vertices.cuda()[src_faces.cuda().long()[face_id.long()]]
    valid = (tri * uvw.unsqueeze(-1)).sum(dim=1)

    C = attr_volume.shape[1]
    attrs = torch.zeros(texture_size, texture_size, C, device='cuda')
    attrs[mask] = grid_sample_3d(
        attr_volume,
        torch.cat([torch.zeros_like(coords[:, :1]), coords], dim=-1),
        shape=torch.Size([1, C, resolution, resolution, resolution]),
        grid=((valid - aabb0) / voxel_size).reshape(1, -1, 3),
        mode='trilinear')

    m_np = mask.cpu().numpy()
    inv = (~m_np).astype(np.uint8)
    out = {}
    for name in ('base_color', 'metallic', 'roughness', 'alpha'):
        sl = attr_layout[name]
        img = np.clip(attrs[..., sl].cpu().numpy() * 255, 0, 255).astype(np.uint8)
        if img.shape[2] == 1:
            img = cv2.inpaint(img[..., 0], inv, 1, cv2.INPAINT_TELEA)[..., None]
        else:
            img = cv2.inpaint(img, inv, 3, cv2.INPAINT_TELEA)
        out[name] = img
    out['mask'] = m_np
    out['fill'] = float(m_np.mean() * 100)
    return out


def unwrap(vertices, faces, texture_size=2048, max_cost=8.0, max_iterations=4,
           normal_seam_weight=0.5, straightness_weight=1.0, padding=4):
    """xatlas で展開する。ADR-0007 のとおり、リトポロジー済みメッシュに当てる前提。"""
    import xatlas
    at = xatlas.Atlas()
    at.add_mesh(np.asarray(vertices, dtype=np.float32), np.asarray(faces, dtype=np.uint32))
    co = xatlas.ChartOptions()
    co.max_cost = max_cost
    co.max_iterations = max_iterations
    co.normal_seam_weight = normal_seam_weight
    co.straightness_weight = straightness_weight
    po = xatlas.PackOptions()
    po.resolution = texture_size
    po.padding = padding
    at.generate(chart_options=co, pack_options=po)
    vmap, idx, uvs = at[0]
    return vmap, idx, uvs
