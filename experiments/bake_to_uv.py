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


# ---------------------------------------------------------------------------
# 視点そろえの UV 展開
#
# ★なぜこれを試すのか（2026-08-30）
#   自動展開は「これは頭」「これは腕」と分かっていないので、歪みの閾値だけで
#   切り刻む。結果 512チャートになった。一方こちらの入力は最初から4方向の絵で、
#   面ごとの視点の重みは ADR-0006 で既に計算している。
#   よく見えている面をその視点へ平面投影すれば、チャートは4枚で済み、
#   しかも【アトラスが元の絵と1対1で対応する】。
#   元絵の貼り直しに位置合わせが要らなくなるのが最大の利点。
#
#   どの視点からも斜めにしか見えない面は歪むので、そこだけ xatlas に回す。
VIEW_DIRS = {
    'front': (0.0, -1.0, 0.0),
    'back':  (0.0,  1.0, 0.0),
    'left':  (1.0,  0.0, 0.0),
    'right': (-1.0, 0.0, 0.0),
}
# 各視点でのアトラス上の平面座標の取り方（法線方向を落として2軸を残す）
VIEW_AXES = {
    'front': (0, 2, +1, +1),   # x, z
    'back':  (0, 2, -1, +1),
    'left':  (1, 2, -1, +1),   # y, z
    'right': (1, 2, +1, +1),
}


def unwrap_view_aligned(vertices, faces, views=('front', 'left', 'right', 'back'),
                        min_facing=0.5, texture_size=2048, margin=0.01):
    """よく見えている面を視点ごとの平面へ投影し、残りは xatlas に回す。

    返り値: (new_vertices, new_faces, uvs, info)
      info['view_faces'] … 視点ごとの面数
      info['residual']   … xatlas に回した面数
    """
    import xatlas
    V = np.asarray(vertices, dtype=np.float64)
    F = np.asarray(faces, dtype=np.int64)
    # 面法線
    p0, p1, p2 = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(p1 - p0, p2 - p0)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = n / np.maximum(ln, 1e-12)
    # 外向きに揃える（重心からの向きで判定）
    c = (p0 + p1 + p2) / 3.0
    cn = c / np.maximum(np.linalg.norm(c, axis=1, keepdims=True), 1e-12)
    n *= np.sign((n * cn).sum(1, keepdims=True) + 1e-12)

    dirs = np.array([VIEW_DIRS[v] for v in views])
    facing = n @ dirs.T                       # (F, V)
    best = facing.argmax(1)
    bestval = facing.max(1)
    assigned = bestval >= min_facing

    # 視点ごとにグリッドを割り当てる（2x2 で4視点）
    cols = int(np.ceil(np.sqrt(len(views) + 1)))
    cell = 1.0 / cols
    new_v, new_f, new_uv = [], [], []
    info = {'view_faces': {}, 'residual': 0}
    vcount = 0
    for vi, vname in enumerate(views):
        sel = assigned & (best == vi)
        info['view_faces'][vname] = int(sel.sum())
        if not sel.any():
            continue
        ax0, ax1, s0, s1 = VIEW_AXES[vname]
        fs = F[sel]
        # ★頂点は視点グループ内で溶接する。面ごとに複製すると
        #   隣り合う面が頂点を共有せず、1面=1チャートになってしまう。
        uniq, inv = np.unique(fs.reshape(-1), return_inverse=True)
        verts = V[uniq]
        uu = verts[:, ax0] * s0
        vv = verts[:, ax1] * s1
        u0, u1 = uu.min(), uu.max()
        v0, v1 = vv.min(), vv.max()
        sc = (1 - 2 * margin) / max(u1 - u0, v1 - v0, 1e-9)
        uu = (uu - u0) * sc + margin
        vv = (vv - v0) * sc + margin
        r, cc = divmod(vi, cols)
        uu = uu * cell + cc * cell
        vv = vv * cell + r * cell
        new_v.append(verts)
        new_uv.append(np.stack([uu, vv], 1))
        new_f.append(inv.reshape(-1, 3) + vcount)
        vcount += len(uniq)

    # 残りは xatlas へ（最後のセルに詰める）
    rest = ~assigned
    info['residual'] = int(rest.sum())
    if rest.any():
        fs = F[rest]
        uniq, inv = np.unique(fs.reshape(-1), return_inverse=True)
        at = xatlas.Atlas()
        at.add_mesh(V[uniq].astype(np.float32), inv.reshape(-1, 3).astype(np.uint32))
        co = xatlas.ChartOptions(); co.max_cost = 8.0; co.max_iterations = 4
        po = xatlas.PackOptions(); po.resolution = texture_size; po.padding = 4
        at.generate(chart_options=co, pack_options=po)
        vmap, idx, uvs = at[0]
        r, cc = divmod(len(views), cols)
        uvs = uvs * cell + np.array([cc * cell, r * cell])
        new_v.append(V[uniq][vmap])
        new_uv.append(uvs)
        new_f.append(idx.astype(np.int64) + vcount)
        vcount += len(vmap)

    return (np.concatenate(new_v).astype(np.float32),
            np.concatenate(new_f).astype(np.int32),
            np.concatenate(new_uv).astype(np.float32),
            info)
