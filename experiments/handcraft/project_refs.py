# 元絵4枚を、UV 付きメッシュのテクスチャへ【直接】投影する（nvdiffrast・venv）。
#   ・正射投影。向きごとに、メッシュのシルエットを絵のシルエットへ合わせる（高さと中心）
#   ・テクセルごとに: 各向きの絵から色を拾い、「面がその向きを向いている度合い」と
#     「手前に隠れていないか（深度）」で重み付けして混ぜる
#   ・どの向きからも見えないテクセルは空のまま（あとで AI 塗りか近傍で埋める）
# 使いかた: venv\Scripts\python.exe project_refs.py メッシュ.glb 絵dir 出力dir [解像度]
import os, sys, json, math
import numpy as np
import torch
import trimesh
from PIL import Image
import nvdiffrast.torch as dr

mesh_path, img_dir, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
TEX = int(sys.argv[4]) if len(sys.argv) > 4 else 2048
VIEW_RES = 1536
FACING_POW = float(os.environ.get('FACING_POW', 6))   # 正面を向いた面ほど重く。★2 だと横の絵の腕が胴に混ざった
MIN_FACING = float(os.environ.get('MIN_FACING', 0.3))  # これ未満の向きからは貼らない
PAD = int(os.environ.get('PAD', 24))                   # 絵の輪郭の外へ色を延ばす px（描画解像度 1536 基準）
PAD_MIN = float(os.environ.get('PAD_MIN', 0.3))        # 延ばした先端での重み
SOLID_ERODE = int(os.environ.get('SOLID_ERODE', 2))    # 延ばす元の色を取る「完全に不透明な内側」の削り px
FILL = os.environ.get('FILL', 'holecolor')             # 埋め方: holecolor（穴ごとに縁の中央値1色）/ coherent（島の中で広げる→局所平滑）/ smooth（距離加重: 泥色になる）/ nearest（旧: 斑）
UV_SS = int(os.environ.get('UV_SS', 2))                # UV ラスタの超解像倍率（島の中の穴を消す）
HOLE_SPLIT = os.environ.get('HOLE_SPLIT', '1') == '1'  # 穴を面の折れ目で分ける（目玉の裏をフードと分ける）
HOLE_SPLIT_DEG = float(os.environ.get('HOLE_SPLIT_DEG', 35))
os.makedirs(out_dir, exist_ok=True)
dev = torch.device('cuda')

# ---- メッシュ（内部規約: Z上・正面 -Y）----
m = trimesh.load(mesh_path, force='mesh', process=False)
V = np.asarray(m.vertices, np.float32); F = np.asarray(m.faces, np.int32)
UV = np.asarray(m.visual.uv, np.float32)
assert UV is not None and len(UV) == len(V), 'UV が頂点ごとに無い'
ctr = (V.max(0) + V.min(0)) / 2; V = V - ctr
h = V[:, 2].max() - V[:, 2].min(); V = V / h            # 高さ 1 に正規化
fn = np.asarray(m.face_normals, np.float32)
vn = np.asarray(m.vertex_normals, np.float32)
# ★NORMAL_REF: 巻きに頼らず法線の向きを決める。生成メッシュは巻きが揃わず（非多様体で伝播も失敗、
#   部品多数決でも参照と一致 70%）、法線が裏向きの面は「カメラを向いていない」と判定されて埋めに回る。
#   面ごとに、向きの揃った参照メッシュ（ボクセルリメッシュ後）の最寄り面と符号を合わせ、
#   その面法線を面積重みで頂点に集めて頂点法線にする（2026-09-05）
if os.environ.get('NORMAL_REF'):
    from scipy.spatial import cKDTree
    ref = trimesh.load(os.environ['NORMAL_REF'], force='mesh', process=False)
    Vr = np.asarray(ref.vertices, np.float32); Vr = (Vr - (Vr.max(0) + Vr.min(0)) / 2); Vr = Vr / (Vr[:, 2].max() - Vr[:, 2].min())
    rc = Vr[np.asarray(ref.faces)].mean(1); rn = np.asarray(ref.face_normals, np.float32)
    _, j = cKDTree(rc).query(V[F].mean(1), k=1, workers=-1)
    sign = np.where(np.einsum('ij,ij->i', fn, rn[j]) < 0, -1.0, 1.0).astype(np.float32)
    fn = fn * sign[:, None]
    area = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    acc_n = np.zeros_like(V)
    for k_ in range(3):
        np.add.at(acc_n, F[:, k_], fn * area[:, None])
    vn = (acc_n / np.maximum(np.linalg.norm(acc_n, axis=1, keepdims=True), 1e-12)).astype(np.float32)
    print(f'法線の向きを参照に合わせた: 反転 {(sign < 0).mean():.1%} の面', flush=True)
print(f'メッシュ: 頂点 {len(V):,} 面 {len(F):,} 高さ→1', flush=True)

Vt = torch.tensor(V, device=dev); Ft = torch.tensor(F, device=dev)
VNt = torch.tensor(vn, device=dev); UVt = torch.tensor(UV, device=dev)
ctx = dr.RasterizeCudaContext()

def view_dir(theta):
    """向き theta（度）のカメラの視線（メッシュ→カメラ）。0=正面(-Y側から見る)。"""
    t = math.radians(theta)
    return np.array([-math.sin(t), -math.cos(t), 0.0], np.float32)   # カメラの位置方向

def to_view(theta):
    """頂点を「カメラが -Y から +Y を見る」座標へ回す。x=右, z=上, y=奥。"""
    t = math.radians(theta)
    c, s = math.cos(t), math.sin(t)
    R = torch.tensor([[c, -s, 0], [s, c, 0], [0, 0, 1]], device=dev, dtype=torch.float32)
    return Vt @ R.T

def raster_view(theta):
    P = to_view(theta)                        # (N,3) 右=x, 奥=y, 上=z
    # クリップ座標: x→x, y→z(上), 深度→y。正射なので w=1。範囲は ±0.75（高さ1に少し余白）
    S = 1 / 0.75
    clip = torch.stack([P[:, 0] * S, P[:, 2] * S, P[:, 1] * 0.5, torch.ones_like(P[:, 0])], 1)[None]
    rast, _ = dr.rasterize(ctx, clip, Ft, (VIEW_RES, VIEW_RES))
    depth, _ = dr.interpolate(P[:, 1:2][None].contiguous(), rast, Ft)   # 奥行き y
    # ★nvdiffrast の画像は【下から上】（OpenGL 流儀）。元絵は上から下。
    #   ここで上下を返して、以降は「行0＝上」で統一する。
    #   返し忘れると可視判定が鏡像の位置を見て、貼れる面が半分に落ちた（実測）
    rast = torch.flip(rast, dims=[1]); depth = torch.flip(depth, dims=[1])
    mask = rast[0, ..., 3] > 0
    return rast, depth[0, ..., 0], mask, S

def fit_image(theta, mask, S, name):
    """絵のシルエットを、メッシュのシルエットの bbox に合わせて描画解像度へ置く。"""
    im = Image.open(os.path.join(img_dir, f'{name}.png')).convert('RGBA')
    a = np.asarray(im)[..., 3]
    ys, xs = np.where(a > 8)
    ib = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    im = im.crop(ib)
    mk = mask.cpu().numpy()
    ys, xs = np.where(mk)
    mb = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    mh, mw = mb[3] - mb[1], mb[2] - mb[0]
    # 高さで合わせる（横幅はポーズ差で一致しないため）
    s = mh / im.height
    new = (max(1, int(round(im.width * s))), mh)
    im = im.resize(new, Image.LANCZOS)
    canvas = Image.new('RGBA', (VIEW_RES, VIEW_RES), (0, 0, 0, 0))
    cx = (mb[0] + mb[2]) // 2 - new[0] // 2
    canvas.alpha_composite(im, (cx, mb[1]))
    arr = np.asarray(canvas).astype(np.float32) / 255.0
    # 一致度（シルエット IoU）
    ia = arr[..., 3] > 0.03
    iou = (ia & mk).sum() / max(1, (ia | mk).sum())
    # ★輪郭の外へ色を延ばす（PAD px）。メッシュの輪郭は絵より太い所があり（IoU 0.9）、
    #   その面は絵の外を指して「貼れない」扱い → 埋めの斑になっていた（2026-09-05 出どころ図で確認）。
    #   外側の画素は最も近い内側の画素の色にし、alpha は 1 → PAD_MIN へ線形に落として重みに使う。
    #   透明画素の RGB はほぼ黒（実測 28,27,26）なので、延ばさないと縁のバイリニアで黒がにじむ
    #   ★延ばす元は【完全に不透明な画素をさらに 2px 削った内側】から取る。縁の半透明画素は
    #   RGB に黒が混ざっており（実測 alpha<0.5 で (28,27,26)）、そこから延ばすと腕の上面に
    #   灰茶の筋が並んだ（2026-09-05 dbg_armtop で確認）。縁の画素自体も同じ内側の色で置き換える
    from scipy import ndimage as _nd
    inside = arr[..., 3] > 0.5
    solid = _nd.binary_erosion(arr[..., 3] > 0.98, iterations=SOLID_ERODE)
    if PAD > 0 and solid.any():
        dist, (iy, ix) = _nd.distance_transform_edt(~solid, return_distances=True, return_indices=True)
        rgb = np.where(solid[..., None], arr[..., :3], arr[..., :3][iy, ix])
        dist_in, _ = _nd.distance_transform_edt(~inside, return_distances=True, return_indices=True)
        a = np.where(inside, 1.0, np.clip(1.0 - dist_in / PAD, 0.0, 1.0) * (1 - PAD_MIN) + PAD_MIN)
        a = np.where(dist_in <= PAD, a, 0.0)
        arr = np.concatenate([rgb, a[..., None].astype(np.float32)], -1)
    dist_out = _nd.distance_transform_edt(~inside).astype(np.float32)   # 絵の外側の画素→輪郭までの px
    return torch.tensor(arr, device=dev), float(iou), torch.tensor(dist_out, device=dev)

# ---- 向きと絵の対応: 4x4 の IoU で決める（名前を信じない）----
views = [('front', 0), ('left', 90), ('back', 180), ('right', 270)]
names = [v for v, _ in views]
rasters = {th: raster_view(th) for _, th in views}
ious = np.zeros((4, 4))
fitted = {}; dist_outs = {}
for i, (_, th) in enumerate(views):
    for j, nm in enumerate(names):
        img, iou, dout = fit_image(th, rasters[th][2], rasters[th][3], nm)
        ious[i, j] = iou; fitted[(th, nm)] = img; dist_outs[(th, nm)] = dout
# ★対応は規約で固定する。T ポーズの前後シルエットはほぼ同じで、IoU では
#   区別できない（実測: front 0.59 / back 0.61 で back が選ばれ、顔が裏に付いた）。
#   内部規約は Z上・正面 -Y。カメラは 0° で -Y 側から見る＝正面。
#   90° は R(+90) で x→-y に回るので、カメラは -X 側＝キャラの右側を見る。
assign = {0: 'front', 90: 'right', 180: 'back', 270: 'left'}
best = [names.index(assign[th]) for _, th in views]
print('向きと絵の対応（規約で固定。IoU は参考値）:', flush=True)
for i in range(4):
    print(f'  {views[i][1]:3d}° ← {assign[views[i][1]]:5s}  IoU {ious[i, best[i]]:.3f}', flush=True)

# ---- デバッグ: 位置合わせの重ね絵（赤=メッシュだけ, 緑=絵だけ, 白=両方）----
for _, th in views:
    mk = rasters[th][2].cpu().numpy()
    ia = (fitted[(th, assign[th])][..., 3] > 0.03).cpu().numpy()
    ov = np.zeros((VIEW_RES, VIEW_RES, 3), np.uint8)
    ov[mk & ~ia] = (255, 60, 60); ov[ia & ~mk] = (60, 200, 60); ov[mk & ia] = (240, 240, 240)
    Image.fromarray(ov).resize((512, 512)).save(os.path.join(out_dir, f'overlay_{th}.png'))

# ---- UV 空間へラスタライズして、テクセルごとの位置と法線を得る ----
uv_clip = torch.cat([UVt * 2 - 1, torch.zeros_like(UVt[:, :1]), torch.ones_like(UVt[:, :1])], 1)[None]
# ★UV_SS 倍で描いて縮める。等倍だと細かい三角形の島の中に「どの三角形の中心も踊らない」
#   テクセルが点々と空き、そこが埋め色の斑になった（2026-09-05 出どころ図で確認）
rast_uv, _ = dr.rasterize(ctx, uv_clip, Ft, (TEX * UV_SS, TEX * UV_SS))
pos_uv, _ = dr.interpolate(Vt[None], rast_uv, Ft)          # (1,T,T,3)
nrm_uv, _ = dr.interpolate(VNt[None], rast_uv, Ft)
# ★UV 空間も下から上で出る。glTF のテクスチャは行0が v=1（上）なので、
#   rasterize 出力を上下に返せば「テクスチャ画像の行 = 1-v」になる
rast_uv = torch.flip(rast_uv, dims=[1]); pos_uv = torch.flip(pos_uv, dims=[1]); nrm_uv = torch.flip(nrm_uv, dims=[1])
cov_ss = (rast_uv[0, ..., 3] > 0).float()
def _pool(x):   # (T*ss, T*ss, C) → (T, T, C) covered な小テクセルの平均
    x = (x * cov_ss[..., None]).permute(2, 0, 1)[None]
    s = torch.nn.functional.avg_pool2d(x, UV_SS)[0].permute(1, 2, 0)
    n = torch.nn.functional.avg_pool2d(cov_ss[None, None], UV_SS)[0, 0]
    return s / n.clamp(min=1e-6)[..., None], n
# ★位置と法線は小テクセルの【平均ではなく代表 1 つ】から取る。島が 2 万個・余白 1px の UV では、
#   1 テクセルの中に別の島（3D では離れた場所）の小テクセルが同居し、平均すると宙に浮いた点になって
#   可視判定に落ち、色も別の場所のものになった（2026-09-05: 貼れた面 37%、胸に橙の四角）
_, cov_frac = _pool(pos_uv[0])
covered = cov_frac > 0
def _rep(x):   # 各テクセルで最初に covered な小テクセルの値
    xs = x.reshape(TEX, UV_SS, TEX, UV_SS, -1).permute(0, 2, 1, 3, 4).reshape(TEX, TEX, UV_SS * UV_SS, -1)
    cs = cov_ss.reshape(TEX, UV_SS, TEX, UV_SS).permute(0, 2, 1, 3).reshape(TEX, TEX, UV_SS * UV_SS)
    first = torch.argmax(cs, dim=-1)                       # 最初の 1（無ければ 0）
    return torch.gather(xs, 2, first[..., None, None].expand(TEX, TEX, 1, xs.shape[-1]))[:, :, 0]
if os.environ.get('UV_REP', '1') == '1':
    pos = _rep(pos_uv[0]); nrm = _rep(nrm_uv[0])
else:
    pos, _ = _pool(pos_uv[0]); nrm, _ = _pool(nrm_uv[0])
nrm = torch.nn.functional.normalize(nrm, dim=-1)
# 自分の三角形 ID（小テクセルごと）: (T, T, ss*ss)。可視判定で「どれか一致」を見る
own_tris = rast_uv[0, ..., 3].reshape(TEX, UV_SS, TEX, UV_SS).permute(0, 2, 1, 3).reshape(TEX, TEX, UV_SS * UV_SS)
print(f'UV の島が占める割合: {covered.float().mean().item():.1%}', flush=True)

def nearest3d_fill(color, known, island):
    """island のうち known でないテクセルを、3D で最も近い known テクセルの色にする。"""
    from scipy.spatial import cKDTree
    P = pos.cpu().numpy().reshape(-1, 3)
    kn = known.cpu().numpy().reshape(-1); isl = island.cpu().numpy().reshape(-1)
    C = color.cpu().numpy().reshape(-1, 3).copy()
    need = isl & ~kn
    if kn.sum() == 0 or need.sum() == 0:
        return torch.tensor(C.reshape(TEX, TEX, 3), device=dev)
    tree = cKDTree(P[kn])
    _, idx = tree.query(P[need], k=1, workers=-1)
    C[need] = C[kn][idx]
    return torch.tensor(C.reshape(TEX, TEX, 3), device=dev)


def smooth_fill(color, known, island, k=32, sym_r=0.015, smooth_iters=6, smooth_k=16):
    """island のうち known でないテクセルを、なだらかに埋める。

    ★最近傍コピー（nearest3d_fill）は、埋める面が広い（帽子の天面・手の甲・股）と
      「一番近い既知テクセル」がまばらに切り替わってボロノイ状の斑になった（2026-09-05 実測）。
      1. 左右対称: 鏡像位置 (-x, y, z) の近く（sym_r 以内）に既知テクセルがあれば、その色を最優先
         （手・肩・足の裏は片側だけ貼れていることが多い）
      2. 3D で近い既知テクセル k 個の距離加重平均（1/d²）
      3. 埋めた面だけを、3D 近傍 smooth_k 個の平均で smooth_iters 回ならす
    """
    from scipy.spatial import cKDTree
    P = pos.cpu().numpy().reshape(-1, 3)
    kn = known.cpu().numpy().reshape(-1); isl = island.cpu().numpy().reshape(-1)
    C = color.cpu().numpy().reshape(-1, 3).copy()
    need_idx = np.where(isl & ~kn)[0]
    if kn.sum() == 0 or len(need_idx) == 0:
        return torch.tensor(C.reshape(TEX, TEX, 3), device=dev)
    kn_idx = np.where(kn)[0]
    tree = cKDTree(P[kn_idx])
    Ck = C[kn_idx]
    out = C.copy()
    n_sym = 0
    for s0 in range(0, len(need_idx), 200000):
        sel = need_idx[s0:s0 + 200000]
        d, nb = tree.query(P[sel], k=k, workers=-1)
        w = 1.0 / (d + 1e-3) ** 2
        Pm = P[sel] * np.array([-1, 1, 1], np.float32)
        dm, nbm = tree.query(Pm, k=8, workers=-1)
        hit = dm[:, 0] < sym_r
        wm = np.where(dm < sym_r, 1.0 / (dm + 1e-3) ** 2, 0.0) * hit[:, None] * 4.0   # 対称は近さ相当で 4 倍重く
        num = (Ck[nb] * w[..., None]).sum(1) + (Ck[nbm] * wm[..., None]).sum(1)
        den = w.sum(1) + wm.sum(1)
        out[sel] = num / den[:, None]
        n_sym += int(hit.sum())
    print(f'  埋め: {len(need_idx):,} テクセル（うち対称の相手あり {n_sym:,}）', flush=True)
    # ならす: 埋めたテクセルだけ更新。近傍は既知＋埋めの全部から取る
    all_idx = np.where(isl)[0]
    tree_all = cKDTree(P[all_idx])
    _, nb_all = tree_all.query(P[need_idx], k=smooth_k, workers=-1)
    for _ in range(smooth_iters):
        out[need_idx] = out[all_idx][nb_all].mean(1)
    return torch.tensor(out.reshape(TEX, TEX, 3), device=dev)


def coherent_fill(color, known, island, smooth_iters=4, smooth_k=12):
    """島の境界から順に広げて埋め、最後に埋めた所だけ局所的にならす。

    ★距離加重平均（smooth_fill）は、股の下でズボンの青・ベルトの茶・足の橙を混ぜて泥色にした。
      最近傍コピー（nearest3d_fill）は帽子の天面や手の甲でボロノイ状の斑になった（いずれも 2026-09-05 実測）。
      1. UV の島の中で、貼れた所から隣へ広げる（grow_in_island）。同じ面の色しか使わないので混ざらない
      2. 島ごと空いている所（帽子の天面の島など）だけ 3D 最近傍コピー
      3. 埋めた所だけ 3D 近傍 smooth_k 個の平均で smooth_iters 回ならし、ボロノイの境目を消す
    """
    from scipy.spatial import cKDTree
    grown, known2 = grow_in_island(color, known, island)
    print(f'  埋め1（島の中で広げる）: {int((known2 & ~known).sum()):,} テクセル', flush=True)
    rest = island & ~known2
    out = nearest3d_fill(grown, known2, island)
    print(f'  埋め2（島ごと空き→3D 最近傍）: {int(rest.sum()):,} テクセル', flush=True)
    P = pos.cpu().numpy().reshape(-1, 3)
    isl = island.cpu().numpy().reshape(-1); need = (island & ~known).cpu().numpy().reshape(-1)
    C = out.cpu().numpy().reshape(-1, 3).copy()
    all_idx = np.where(isl)[0]; need_idx = np.where(need)[0]
    if len(need_idx) and len(all_idx) > smooth_k:
        tree = cKDTree(P[all_idx])
        _, nb = tree.query(P[need_idx], k=smooth_k, workers=-1)
        for _ in range(smooth_iters):
            C[need_idx] = C[all_idx][nb].mean(1)
    return torch.tensor(C.reshape(TEX, TEX, 3), device=dev)


def holecolor_fill(color, known, island, min_hole=64, band=6, blend_iters=8):
    """穴ごとに「その縁で見えている色の中央値」1色で塗り、境目だけなじませる。

    ★利用者の指摘「見えない部分は大体の想像をすれば同じ色」（2026-09-05）。
      島の中で広げる方式（coherent_fill）は、目玉の裏に白と隣のフードの橙が筋になって混ざった。
      穴の縁の中央値なら、縁の大半が白の目玉は白、帽子の天面は茶、股はズボンの青になる。
      1. UV 上で「島の中の未知テクセル」を連結成分（穴）に分ける
      2. 穴ごとに、1 テクセル外側の既知テクセルの色の中央値で塗る（小さい穴は島の中で広げる）
      3. 島ごと空いている所は 3D 最近傍
      4. 穴の縁 band テクセルだけ 3D 近傍平均でなじませる
    """
    from scipy import ndimage as _nd
    from scipy.spatial import cKDTree
    C = color.cpu().numpy().copy(); kn = known.cpu().numpy(); isl = island.cpu().numpy()
    unknown = isl & ~kn
    if HOLE_SPLIT:
        # ★穴を「面の折れ目」で分ける。目玉の裏とフードの裏は UV では 1 つの穴だが、
        #   3D では目玉（球）とフードの境に折れ目がある。隣接テクセルの法線が HOLE_SPLIT_DEG 以上
        #   違う所や 3D で離れている所は繋がないで連結成分を取る → 目玉は目玉の縁の色（白）だけで埋まる
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        N = nrm.cpu().numpy(); Pp = pos.cpu().numpy()
        idx = -np.ones((TEX, TEX), np.int64); ui = np.where(unknown.reshape(-1))[0]; idx.reshape(-1)[ui] = np.arange(len(ui))
        cosmin = math.cos(math.radians(HOLE_SPLIT_DEG)); dmax = 3.0 / TEX
        rows = []; cols = []
        for dy, dx in ((0, 1), (1, 0)):
            a = idx[:TEX - dy, :TEX - dx]; b = idx[dy:, dx:]
            ok = (a >= 0) & (b >= 0)
            na = N[:TEX - dy, :TEX - dx][ok]; nb = N[dy:, dx:][ok]
            pa = Pp[:TEX - dy, :TEX - dx][ok]; pb = Pp[dy:, dx:][ok]
            keep = ((na * nb).sum(-1) > cosmin) & (np.linalg.norm(pa - pb, axis=-1) < dmax)
            rows.append(a[ok][keep]); cols.append(b[ok][keep])
        rows = np.concatenate(rows); cols = np.concatenate(cols)
        g = coo_matrix((np.ones(len(rows), np.uint8), (rows, cols)), shape=(len(ui), len(ui)))
        n, cl = connected_components(g, directed=False)
        lab = np.zeros((TEX, TEX), np.int64); lab.reshape(-1)[ui] = cl + 1
    else:
        lab, n = _nd.label(unknown, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel())
    # 縁: 未知の 1 テクセル外側にある既知テクセル。穴ラベルは膨張で伝える
    dil = _nd.grey_dilation(lab, size=(3, 3))
    rim = kn & (dil > 0)
    rim_lab = dil[rim]; rim_col = C[rim]
    order_ = np.argsort(rim_lab, kind='stable'); rim_lab = rim_lab[order_]; rim_col = rim_col[order_]
    starts = np.searchsorted(rim_lab, np.arange(1, n + 1)); ends = np.searchsorted(rim_lab, np.arange(1, n + 1), side='right')
    med = np.zeros((n + 1, 3), np.float32); has = np.zeros(n + 1, bool)
    for i in range(1, n + 1):
        if ends[i - 1] > starts[i - 1]:
            med[i] = np.median(rim_col[starts[i - 1]:ends[i - 1]], axis=0); has[i] = True
    big = (sizes >= min_hole) & has
    big[0] = False
    fill_mask = big[lab]
    C[fill_mask] = med[lab[fill_mask]]
    print(f'  埋め（穴の縁の中央値で1色）: 穴 {int(big.sum()):,} 個 / {int(fill_mask.sum()):,} テクセル', flush=True)
    known2 = torch.tensor(kn | fill_mask, device=dev)
    color2 = torch.tensor(C, device=dev)
    # 小さい穴と縁なし（島ごと空き）はこれまでの方式
    grown, known3 = grow_in_island(color2, known2, island)
    rest = island & ~known3
    out = nearest3d_fill(grown, known3, island)
    print(f'  埋め（小さい穴を島の中で広げる）: {int((known3 & ~known2).sum()):,} / 島ごと空き→3D 最近傍: {int(rest.sum()):,}', flush=True)
    # 境目をなじませる: 穴の縁から band 以内（穴側・既知側とも）
    edge = _nd.binary_dilation(fill_mask, iterations=band) & ~_nd.binary_erosion(fill_mask, iterations=band) & isl
    P = pos.cpu().numpy().reshape(-1, 3); Co = out.cpu().numpy().reshape(-1, 3).copy()
    all_idx = np.where(isl.reshape(-1))[0]; e_idx = np.where(edge.reshape(-1))[0]
    if len(e_idx) and len(all_idx) > 16:
        tree = cKDTree(P[all_idx]); _, nb = tree.query(P[e_idx], k=16, workers=-1)
        for _ in range(blend_iters):
            Co[e_idx] = Co[all_idx][nb].mean(1)
    return torch.tensor(Co.reshape(TEX, TEX, 3), device=dev)


def holecolor3d_fill(color, known, island, min_hole=64, band=6, blend_iters=8, k=8, split_deg=35.0):
    """holecolor_fill の 3D 版。穴の連結・縁・なじませを UV ではなく 3D 近傍グラフで取る。

    ★島が数万個に砕けた UV（xatlas を 44 万三角形に掛けた場合）では、1 つの穴が島ごとに
      分断され、穴ごとの色がバラバラの継ぎはぎになった（2026-09-05 実測）。
      3D で近いテクセル同士（法線も近い）を繋げば、島の境を越えて 1 つの穴として扱える。
    """
    from scipy.spatial import cKDTree
    from scipy.sparse import coo_matrix, csr_matrix
    from scipy.sparse.csgraph import connected_components
    isl = island.cpu().numpy().reshape(-1); kn = known.cpu().numpy().reshape(-1)
    P = pos.cpu().numpy().reshape(-1, 3); N = nrm.cpu().numpy().reshape(-1, 3)
    C = color.cpu().numpy().reshape(-1, 3).copy()
    ii = np.where(isl)[0]; Pi = P[ii]; Ni = N[ii]; kni = kn[ii]
    tree = cKDTree(Pi)
    d, nb = tree.query(Pi, k=k + 1, workers=-1)
    d = d[:, 1:]; nb = nb[:, 1:]
    spacing = float(np.median(d[:, 0]))
    src_ = np.repeat(np.arange(len(ii)), k); dst_ = nb.reshape(-1); dd = d.reshape(-1)
    # ★距離の上限は 6 倍。2.5 倍だと 4096² で細かい三角形の島境のテクセルが孤立し、
    #   なじませの平均が 0 になって黒い斑が出た（2026-09-05 実測）
    ok = (dd < 6.0 * spacing) & ((Ni[src_] * Ni[dst_]).sum(-1) > math.cos(math.radians(split_deg)))
    src_, dst_ = src_[ok], dst_[ok]
    n_i = len(ii)
    A = csr_matrix((np.ones(len(src_), np.float32), (src_, dst_)), shape=(n_i, n_i)); A = A.maximum(A.T)
    unk = ~kni
    # 未知同士の辺だけで連結成分
    Au = A[unk][:, unk]
    ncomp, lab_u = connected_components(Au, directed=False)
    lab = np.full(n_i, -1); lab[unk] = lab_u
    # 縁: 既知テクセルのうち未知に隣接するもの → 隣接する未知の成分に色を投票
    Ak = A[unk][:, ~unk]                       # 行=未知, 列=既知
    rows, cols = Ak.nonzero()                  # 未知 rows が 既知 cols と隣接
    comp_of = lab_u[rows]; kcol = C[ii[~unk][cols]]
    order_ = np.argsort(comp_of, kind='stable'); comp_s = comp_of[order_]; kcol = kcol[order_]
    starts = np.searchsorted(comp_s, np.arange(ncomp)); ends = np.searchsorted(comp_s, np.arange(ncomp), side='right')
    sizes = np.bincount(lab_u, minlength=ncomp)
    med = np.zeros((ncomp, 3), np.float32); has = ends > starts
    for c_ in np.where(has)[0]:
        med[c_] = np.median(kcol[starts[c_]:ends[c_]], axis=0)
    big = has & (sizes >= min_hole)
    fill_u = big[lab_u]
    Cu_idx = ii[unk]
    C[Cu_idx[fill_u]] = med[lab_u[fill_u]]
    print(f'  埋め3D（穴の縁の中央値で1色）: 穴 {int(big.sum()):,} 個 / {int(fill_u.sum()):,} テクセル（テクセル間隔 {spacing:.5f}）', flush=True)
    # 残り（小さい穴・縁なし）: グラフ上で既知から伸ばす（反復平均）。既知にならないものは 3D 最近傍
    known_now = kni.copy(); known_now[np.where(unk)[0][fill_u]] = True
    Ci = C[ii]
    for _ in range(64):
        rest = ~known_now
        if not rest.any():
            break
        Wk = A[rest][:, known_now]
        cnt = np.asarray(Wk.sum(1)).ravel()
        got = cnt > 0
        if not got.any():
            break
        newc = (Wk @ Ci[known_now]) / np.maximum(cnt, 1)[:, None]
        ridx = np.where(rest)[0][got]
        Ci[ridx] = newc[got]; known_now[ridx] = True
    left = ~known_now
    if left.any():
        t2 = cKDTree(Pi[known_now]); _, j = t2.query(Pi[left], k=1, workers=-1)
        Ci[left] = Ci[known_now][j]
    print(f'  埋め3D（伸ばし＋最近傍）: {int((~kni).sum() - fill_u.sum()):,} テクセル', flush=True)
    # なじませ: 1色で塗った穴の縁から band ホップ以内を、グラフ近傍の平均で反復
    fm = np.zeros(n_i, bool); fm[np.where(unk)[0][fill_u]] = True
    edge = fm.copy(); front = fm.copy()
    for _ in range(band):
        front = (A @ front.astype(np.float32)) > 0
        edge |= front
    inner = fm.copy()
    for _ in range(band):
        inner &= ~(((A @ (~inner).astype(np.float32)) > 0))
    edge &= ~inner
    e_idx = np.where(edge)[0]
    if len(e_idx):
        Ae = A[e_idx]; cnt = np.asarray(Ae.sum(1)).ravel()
        okc = cnt > 0                          # ★隣が無いテクセルは触らない（0 で割ると黒になる）
        Ae = Ae[okc]; e_idx = e_idx[okc]; cnt = cnt[okc][:, None]
        for _ in range(blend_iters):
            Ci[e_idx] = (Ae @ Ci) / cnt
    C[ii] = Ci
    return torch.tensor(C.reshape(TEX, TEX, 3), device=dev)


def clean_outliers3d(color, known, k=24, thresh=0.25):
    """3D 近傍 k 個の色の中央値から遠いテクセルを中央値に置き換える（小さな斑を消す）。"""
    from scipy.spatial import cKDTree
    P = pos.cpu().numpy().reshape(-1, 3)
    kn = known.cpu().numpy().reshape(-1)
    C = color.cpu().numpy().reshape(-1, 3).copy()
    idx_known = np.where(kn)[0]
    if len(idx_known) < k + 1:
        return color
    tree = cKDTree(P[idx_known])
    # メモリのため分割して問い合わせる
    out = C.copy(); changed = 0
    for s0 in range(0, len(idx_known), 200000):
        sel = idx_known[s0:s0 + 200000]
        _, nb = tree.query(P[sel], k=k + 1, workers=-1)
        med = np.median(C[idx_known][nb[:, 1:]], axis=1)
        far = np.linalg.norm(C[sel] - med, axis=1) > thresh
        out[sel[far]] = med[far]; changed += int(far.sum())
    print(f'  外れ値の置き換え: {changed:,} テクセル', flush=True)
    return torch.tensor(out.reshape(TEX, TEX, 3), device=dev)


def grow_in_island(color, known, island):
    """known の色を、island の内側でだけ隣へ広げる（隣の島の色を借りない）。"""
    color = color.clone(); known = known.clone()
    k = torch.ones((1, 1, 3, 3), device=dev)
    for _ in range(TEX):
        if bool((island & ~known).sum() == 0):
            break
        num = torch.nn.functional.conv2d((color * known[..., None]).permute(2, 0, 1)[None],
                                         k.expand(3, 1, 3, 3), padding=1, groups=3)[0]
        den = torch.nn.functional.conv2d(known.float()[None, None], k, padding=1)[0, 0]
        grow = (~known) & island & (den > 0)
        if not bool(grow.any()):
            break
        color[grow] = (num.permute(1, 2, 0) / den.clamp(min=1)[..., None])[grow]
        known = known | grow
    return color, known


# ---- 各向きから色を拾って混ぜる ----
# ★前後を先に、横をあとに。横の絵では「カメラ側に伸びた腕」が胴を隠していて、
#   メッシュの腕が元絵の腕と重ならない所で胴にオレンジが乗る（実測）。
#   前後で決まった基準色と食い違う横の色は捨てる。
acc = torch.zeros((TEX, TEX, 3), device=dev); wsum = torch.zeros((TEX, TEX), device=dev)
phantom = torch.zeros((TEX, TEX), dtype=torch.bool, device=dev)
#   ★結果: 逆効果。服の側面（正面からは腕に隠れる）まで捨てて肌色の埋めが広がった。既定は切る
OCC_COLOR = os.environ.get('OCC_COLOR', '0') == '1'
OCC_COLOR_TOL = float(os.environ.get('OCC_COLOR_TOL', 0.15))
occl = {}
PHANTOM_PX = float(os.environ.get('PHANTOM_PX', 12))
per_view = {}
MAXDIFF = float(os.environ.get('MAXDIFF', 0.25))       # 基準色との許容差（RGB 距離）
SIDE_W = float(os.environ.get('SIDE_W', 0.5))          # 横の絵の重み
order = [0, 180, 90, 270]
base_col = None; base_w = None
# ★合議（横の絵にだけ適用）: 前後の絵が「かすった角度でも見えている」色で一致し、横の絵の色が
#   それと大きく違うなら、横の色を捨てて埋めに回す。横の絵では手が胴に重なって描かれ、
#   メッシュの手の断面が絵の手より大きい所で、指先が服の水色を拾った（2026-09-05 実測）。
#   正面から見た瞳のような「正面の絵だけが持つ細部」は前後の絵が高信頼なので巻き込まれない
#   ★結果: 効かなかった。指先の下面は前後の絵から全く見えず（cos≈0）、比較相手が無い。
#     既定は切る。指先の水色は既知の限界として残す
CONSENSUS = os.environ.get('CONSENSUS', '0') == '1'
relaxed = {}
if CONSENSUS:
    for th in (0, 180):
        rast_, depth_, mask_, S_ = rasters[th]
        img_ = fitted[(th, assign[th])]
        t_ = math.radians(th); c_, s2 = math.cos(t_), math.sin(t_)
        R_ = torch.tensor([[c_, -s2, 0], [s2, c_, 0], [0, 0, 1]], device=dev)
        P_ = pos @ R_.T
        px_ = (P_[..., 0] * S_ * 0.5 + 0.5) * VIEW_RES; py_ = (1 - (P_[..., 2] * S_ * 0.5 + 0.5)) * VIEW_RES
        grid_ = torch.stack([px_ / VIEW_RES * 2 - 1, py_ / VIEW_RES * 2 - 1], -1)[None]
        d_ = torch.nn.functional.grid_sample(depth_[None, None], grid_, align_corners=False, mode='nearest')[0, 0]
        tri_ = torch.nn.functional.grid_sample(rast_[0, ..., 3][None, None], grid_, align_corners=False, mode='nearest')[0, 0]
        vis_ = ((tri_ > 0) & (tri_[..., None] == own_tris).any(-1)) | (P_[..., 1] <= d_ + 0.02)
        col_ = torch.nn.functional.grid_sample(img_.permute(2, 0, 1)[None], grid_, align_corners=False)[0].permute(1, 2, 0)
        cos_ = (nrm @ torch.tensor(view_dir(th), device=dev)).clamp(min=0)
        ok_ = covered & vis_ & (col_[..., 3] > 0.95) & (cos_ > 0.15)
        relaxed[th] = (col_[..., :3], ok_)
for th in order:
    rast, depth, mask, S = rasters[th]
    img = fitted[(th, assign[th])]
    t = math.radians(th); c, s_ = math.cos(t), math.sin(t)
    R = torch.tensor([[c, -s_, 0], [s_, c, 0], [0, 0, 1]], device=dev)
    P = pos @ R.T                                        # テクセルの位置をこの向きのカメラ座標へ
    px = (P[..., 0] * S * 0.5 + 0.5) * VIEW_RES          # 右
    py = (1 - (P[..., 2] * S * 0.5 + 0.5)) * VIEW_RES    # 上（画像は上が0）
    # 可視: この向きの深度バッファと比べる
    grid = torch.stack([px / VIEW_RES * 2 - 1, py / VIEW_RES * 2 - 1], -1)[None]
    d_seen = torch.nn.functional.grid_sample(depth[None, None], grid, align_corners=False, mode='nearest')[0, 0]
    # ★可視判定は「その画素を覆っている三角形が自分」なら即 OK。深度比較だけだと
    #   かすった角度で画素の中心との深度差が許容を超え、見えている面を捨てていた
    #   （実測: 貼れたテクセルが島の 4〜6 割しか無かった）
    tri_view = torch.nn.functional.grid_sample(rast[0, ..., 3][None, None], grid, align_corners=False, mode='nearest')[0, 0]
    same_tri = (tri_view > 0) & (tri_view[..., None] == own_tris).any(-1)
    visible = same_tri | (P[..., 1] <= d_seen + float(os.environ.get('DEPTH_TOL', 0.02)))
    # ★遮蔽境界の膨張。元絵の腕はメッシュの腕より太いことがあり、腕の縁のすぐ外の
    #   画素（元絵では腕＝オレンジ）を、メッシュでは胴の面が拾ってしまう。
    #   「K px 以内に自分よりずっと手前の面がある」画素は捨てる
    K = int(os.environ.get('OCC_K', 31))
    dep = torch.where(mask, depth, torch.full_like(depth, 1e3))
    dmin = -torch.nn.functional.max_pool2d(-dep[None, None], K, stride=1, padding=K // 2)[0, 0]
    dmin_s = torch.nn.functional.grid_sample(dmin[None, None], grid, align_corners=False, mode='nearest')[0, 0]
    near_occluder = P[..., 1] > dmin_s + float(os.environ.get('OCC_TOL', 0.03))
    visible = visible & ~near_occluder
    # 向き: 法線とカメラ方向
    vd = torch.tensor(view_dir(th), device=dev)
    cosang = (nrm @ vd).clamp(min=0)
    # ★正規化: (cos-MIN)/(1-MIN) を FACING_POW 乗。MIN ちょうどで重み 0 になるので、
    #   かすった角度の面は自然に「貼れていない」扱い → 埋めに回る。
    #   cos^p のままだと 60° の面にも重みが残り、顎の下に口が引き伸ばされた（2026-09-05 実測）
    facing = ((cosang - MIN_FACING) / (1 - MIN_FACING)).clamp(min=0) ** FACING_POW
    # 絵の色と alpha
    col = torch.nn.functional.grid_sample(img.permute(2, 0, 1)[None], grid, align_corners=False)[0]
    col = col.permute(1, 2, 0)
    # alpha は fit_image で「絵の内側=1、外側は PAD px まで線形に PAD_MIN へ」にしてある
    inside = col[..., 3] > 0.05
    w = facing * visible.float() * inside.float() * col[..., 3] * covered.float()
    # ★遮蔽物の色と同じ横の色は捨てる（OCC_COLOR）。顎の下の胸は正面からはフード/顎に隠れて見えず、
    #   横の絵だけが貼る。横の絵ではそこにフードが描かれているので胸が橙になった（2026-09-05 実測）。
    #   正面の絵で「その位置を隠している物の色」を覚えておき、横の絵の色がそれと同じなら
    #   横の絵も同じ物（フード）を見ていると判断して捨て、埋めに回す（周りの胸の青が入る）
    if th in (0, 180) and OCC_COLOR:
        occl[th] = (covered & inside & ~visible, col[..., :3].clone())
    if th in (90, 270):
        w = w * SIDE_W
        if OCC_COLOR:
            rej = torch.zeros_like(w, dtype=torch.bool)
            for th0, (occ_m, occ_c) in occl.items():
                rej |= occ_m & ((col[..., :3] - occ_c).norm(dim=-1) < OCC_COLOR_TOL)
            rej &= w > 0
            print(f'  {th:3d}°: 遮蔽物と同じ色なので捨てた横の色 {int(rej.sum()):,} テクセル', flush=True)
            w = torch.where(rej, torch.zeros_like(w), w)
        if CONSENSUS:
            (cf, okf), (cb, okb) = relaxed[0], relaxed[180]
            agree = okf & okb & ((cf - cb).norm(dim=-1) < float(os.environ.get('CONS_AGREE', 0.15)))
            differ = (col[..., :3] - (cf + cb) / 2).norm(dim=-1) > float(os.environ.get('CONS_DIFF', 0.35))
            rej = agree & differ & (w > 0)
            print(f'  {th:3d}°: 合議で捨てた横の色 {int(rej.sum()):,} テクセル', flush=True)
            w = torch.where(rej, torch.zeros_like(w), w)
        if base_col is not None and os.environ.get('GATE', '1') == '1':
            has_base = base_w > 0.01
            diff = (col[..., :3] - base_col).norm(dim=-1)
            reject = has_base & (diff > MAXDIFF)
            w = torch.where(reject, torch.zeros_like(w), w)
    acc += col[..., :3] * w[..., None]; wsum += w
    # ★幻の面: この向きから【見えている】のに、絵ではそこに何も無い（輪郭から PHANTOM_PX 以上外）
    #   → メッシュが絵より膨らんでいる場所。どの絵の色も当てにならないので、後で埋めに回す。
    #   横の絵で手が胴に重なる所: メッシュの手が絵の手より長く、指先が服の水色を拾った（2026-09-05 実測）。
    #   正面から見ればその指先は絵の手の外にあるので、ここで拾える
    if PHANTOM_PX > 0:
        dout_s = torch.nn.functional.grid_sample(dist_outs[(th, assign[th])][None, None], grid, align_corners=False)[0, 0]
        strict_vis = same_tri | (P[..., 1] <= d_seen + 0.01)
        phantom |= covered & strict_vis & (dout_s > PHANTOM_PX)
    # ---- デバッグ: どの向きが主に色を決めたか、絵の輪郭から何 px の画素を拾ったか ----
    if os.environ.get('DEBUG_SRC') == '1':
        from scipy import ndimage as _nd
        if 'src_best' not in globals():
            src_best = torch.zeros((TEX, TEX), device=dev); src_view = torch.full((TEX, TEX), -1, device=dev)
            src_edge = torch.zeros((TEX, TEX), device=dev)
        dist_np = _nd.distance_transform_edt((img[..., 3] > 0.5).cpu().numpy()).astype(np.float32)
        dist_s = torch.nn.functional.grid_sample(torch.tensor(dist_np, device=dev)[None, None], grid, align_corners=False)[0, 0]
        better = w > src_best
        src_best = torch.where(better, w, src_best)
        src_view = torch.where(better, torch.full_like(src_view, th), src_view)
        src_edge = torch.where(better, dist_s, src_edge)
    if th == 180:                                        # 前後が終わった時点の基準色
        # ★基準色は【3D 空間で最も近い】前後から見えた面の色。UV の島で広げると
        #   兜の茶がフードに乗った（実測）。3D なら胴の側面の隣は胴の前後＝水色
        fb_known = wsum > float(os.environ.get('FILL_MIN_W', 0.05))
        base_col = nearest3d_fill(acc / wsum.clamp(min=1e-6)[..., None], fb_known, covered)
        base_w = covered.float()
    per_view[th] = float((w > 0.01).float().sum() / covered.float().sum())
    if os.environ.get('DEBUG_SRC') == '1':
        bad = covered & (cosang >= MIN_FACING) & inside & ~visible
        gap = (P[..., 1] - d_seen)[bad]
        if gap.numel():
            q = torch.quantile(gap.float()[:200000], torch.tensor([0.1, 0.5, 0.9], device=dev))
            print(f'    隠れ判定で落ちた面の奥行き差 (P.y - d_seen): 10% {q[0]:.4f} / 50% {q[1]:.4f} / 90% {q[2]:.4f}  件数 {int(bad.sum()):,}', flush=True)
        cv = covered.float().sum()
        print(f'    内訳 {th}: cos>MIN {((cosang >= MIN_FACING) & covered).float().sum() / cv:.1%} '
              f'visible {(visible & covered).float().sum() / cv:.1%} same_tri {(same_tri & covered).float().sum() / cv:.1%} '
              f'inside {(inside & covered).float().sum() / cv:.1%} all {((cosang >= MIN_FACING) & visible & inside & covered).float().sum() / cv:.1%}', flush=True)
    print(f'  {th:3d}° ({assign[th]}): 貼れたテクセル {per_view[th]:.1%}', flush=True)

if PHANTOM_PX > 0:
    print(f'  幻の面（メッシュが絵より膨らんでいる）: {int((phantom & (wsum > 0)).sum()):,} テクセルを埋めに回す', flush=True)
    wsum = torch.where(phantom, torch.zeros_like(wsum), wsum)
albedo = acc / wsum.clamp(min=1e-6)[..., None]

# ---- デバッグ: 向きごとに単独で貼った結果を書く（どの絵が悪さをしているか見る）----
ONLY = os.environ.get('ONLY_VIEW')
if ONLY is not None:
    th = int(ONLY)
    rast, depth, mask, S = rasters[th]
    img = fitted[(th, assign[th])]
    t = math.radians(th); c, s_ = math.cos(t), math.sin(t)
    R = torch.tensor([[c, -s_, 0], [s_, c, 0], [0, 0, 1]], device=dev)
    P = pos @ R.T
    px = (P[..., 0] * S * 0.5 + 0.5) * VIEW_RES; py = (1 - (P[..., 2] * S * 0.5 + 0.5)) * VIEW_RES
    grid = torch.stack([px / VIEW_RES * 2 - 1, py / VIEW_RES * 2 - 1], -1)[None]
    d_seen = torch.nn.functional.grid_sample(depth[None, None], grid, align_corners=False)[0, 0]
    visible = (P[..., 1] <= d_seen + 0.008)
    vd = torch.tensor(view_dir(th), device=dev)
    facing = (nrm @ vd).clamp(min=0)
    col = torch.nn.functional.grid_sample(img.permute(2, 0, 1)[None], grid, align_corners=False)[0].permute(1, 2, 0)
    w = (facing >= MIN_FACING).float() * visible.float() * (col[..., 3] > 0.5).float() * covered.float()
    albedo = col[..., :3]
    wsum = w
# ★信頼度の低いテクセル（かすった角度からしか色が無い）は「貼れていない」扱いにして、
#   島の中の確かな色で埋め直す。前後の絵で法線が横を向いた胴の側面に、
#   元絵の腕の画素が重なってオレンジの斑になっていた（実測）
FILL_MIN_W = float(os.environ.get('FILL_MIN_W', 0.05))
filled = (wsum > FILL_MIN_W)
print(f'合計: 何かの向きから貼れたテクセル {(filled & covered).float().sum() / covered.float().sum():.1%}', flush=True)

# 空きを埋める。★UV 空間の最近傍で埋めてはいけない。隣の島（腕）の色が
#   胴に入って、オレンジの斑になった（実測）。
#   1. まず【島の中だけ】で広げる（covered の内側でだけ膨張）
#   2. 島の外（すき間）は最後に最近傍で埋める（バイリニアのにじみ対策）
# ★塗り残しは【3D で最も近い】貼れたテクセルの色で埋める。UV の島で広げると
#   足のオレンジが股に入った（実測）。3D なら股の隣はズボン＝水色
if FILL == 'holecolor3d':
    alb = holecolor3d_fill(albedo, filled & covered, covered, min_hole=int(os.environ.get('MIN_HOLE', 64)),
                           band=int(os.environ.get('BLEND_BAND', 6)), split_deg=HOLE_SPLIT_DEG)
elif FILL == 'holecolor':
    alb = holecolor_fill(albedo, filled & covered, covered, min_hole=int(os.environ.get('MIN_HOLE', 64)),
                         band=int(os.environ.get('BLEND_BAND', 6)))
elif FILL == 'coherent':
    alb = coherent_fill(albedo, filled & covered, covered, smooth_iters=int(os.environ.get('SMOOTH_ITERS', 4)), smooth_k=int(os.environ.get('SMOOTH_K', 12)))
elif FILL == 'smooth':
    alb = smooth_fill(albedo, filled & covered, covered,
                      sym_r=float(os.environ.get('SYM_R', 0.015)), smooth_iters=int(os.environ.get('SMOOTH_ITERS', 6)))
else:
    alb = nearest3d_fill(albedo, filled & covered, covered)
fmask = covered.clone()
cov = covered
if os.environ.get('CLEAN', '1') == '1':
    alb = clean_outliers3d(alb, covered, k=int(os.environ.get('CLEAN_K', 24)),
                           thresh=float(os.environ.get('CLEAN_T', 0.25)))
alb = alb.cpu().numpy(); fmask_np = fmask.cpu().numpy(); cov_np = cov.cpu().numpy()
from scipy import ndimage
if (~fmask_np).any():
    _, (iy, ix) = ndimage.distance_transform_edt(~fmask_np, return_distances=True, return_indices=True)
    alb_filled = alb[iy, ix]
else:
    alb_filled = alb
fmask = torch.tensor(fmask_np)
Image.fromarray((np.clip(alb_filled, 0, 1) * 255).astype(np.uint8)).save(os.path.join(out_dir, 'albedo.png'))
Image.fromarray(((filled & covered).cpu().numpy() * 255).astype(np.uint8)).save(os.path.join(out_dir, 'coverage.png'))
if os.environ.get('DEBUG_SRC') == '1':
    # 赤=前, 緑=後, 青=右(90), 黄=左(270), 灰=どこからも貼れず埋めた, 紫=貼れたが絵の輪郭 4px 以内の画素
    srcimg = np.zeros((TEX, TEX, 3), np.uint8)
    sv = src_view.cpu().numpy(); fl = (filled & covered).cpu().numpy(); ed = src_edge.cpu().numpy()
    srcimg[cov_np] = (110, 110, 110)
    for th_, colr in ((0, (230, 60, 60)), (180, (60, 200, 60)), (90, (60, 90, 230)), (270, (230, 220, 50))):
        srcimg[fl & (sv == th_)] = colr
    srcimg[fl & (ed <= 4)] = (220, 60, 220)
    Image.fromarray(srcimg).save(os.path.join(out_dir, 'source.png'))
    stats = {int(th_): float((fl & (sv == th_)).sum() / max(1, cov_np.sum())) for th_ in (0, 90, 180, 270)}
    stats['fill'] = float((cov_np & ~fl).sum() / max(1, cov_np.sum())); stats['edge<=4px'] = float((fl & (ed <= 4)).sum() / max(1, cov_np.sum()))
    print('  出どころ（島の中の割合）:', {k: f'{v:.1%}' for k, v in stats.items()}, flush=True)
json.dump({'assign': {str(k): v for k, v in assign.items()},
           'iou': {views[i][0]: float(ious[i, best[i]]) for i in range(4)},
           'per_view': {str(k): v for k, v in per_view.items()}},
          open(os.path.join(out_dir, 'report.json'), 'w'), indent=2)

# テクスチャ付き glb を書く（内部規約のまま）
from trimesh.visual.material import PBRMaterial
mat = PBRMaterial(baseColorTexture=Image.open(os.path.join(out_dir, 'albedo.png')),
                  metallicFactor=0.0, roughnessFactor=0.9)
# ★書き出す glb にも、参照で揃えた向きを入れる（巻きを反転＋頂点法線を保存）。
#   巻きが不一致のままだと Eevee/ビューアが裏向きの面を暗く描き、黒い斑に見えた（2026-09-05 実測。
#   テクスチャ自体には黒は無かった）。両面描画も指定して裏面カリングの事故を防ぐ
F_out = F.copy()
if os.environ.get('NORMAL_REF'):
    F_out[sign < 0] = F_out[sign < 0][:, ::-1]
mat.doubleSided = True
out = trimesh.Trimesh(vertices=m.vertices, faces=F_out, vertex_normals=vn, process=False,
                      visual=trimesh.visual.TextureVisuals(uv=UV, material=mat))
out.export(os.path.join(out_dir, 'projected.glb'))
print('保存:', os.path.join(out_dir, 'projected.glb'), flush=True)
