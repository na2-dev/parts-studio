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
    return torch.tensor(arr, device=dev), float(iou)

# ---- 向きと絵の対応: 4x4 の IoU で決める（名前を信じない）----
views = [('front', 0), ('left', 90), ('back', 180), ('right', 270)]
names = [v for v, _ in views]
rasters = {th: raster_view(th) for _, th in views}
ious = np.zeros((4, 4))
fitted = {}
for i, (_, th) in enumerate(views):
    for j, nm in enumerate(names):
        img, iou = fit_image(th, rasters[th][2], rasters[th][3], nm)
        ious[i, j] = iou; fitted[(th, nm)] = img
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
rast_uv, _ = dr.rasterize(ctx, uv_clip, Ft, (TEX, TEX))
pos_uv, _ = dr.interpolate(Vt[None], rast_uv, Ft)          # (1,T,T,3)
nrm_uv, _ = dr.interpolate(VNt[None], rast_uv, Ft)
# ★UV 空間も下から上で出る。glTF のテクスチャは行0が v=1（上）なので、
#   rasterize 出力を上下に返せば「テクスチャ画像の行 = 1-v」になる
rast_uv = torch.flip(rast_uv, dims=[1]); pos_uv = torch.flip(pos_uv, dims=[1]); nrm_uv = torch.flip(nrm_uv, dims=[1])
covered = rast_uv[0, ..., 3] > 0
pos = pos_uv[0]; nrm = torch.nn.functional.normalize(nrm_uv[0], dim=-1)
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
per_view = {}
MAXDIFF = float(os.environ.get('MAXDIFF', 0.25))       # 基準色との許容差（RGB 距離）
SIDE_W = float(os.environ.get('SIDE_W', 0.5))          # 横の絵の重み
order = [0, 180, 90, 270]
base_col = None; base_w = None
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
    own_tri = rast_uv[0, ..., 3]
    same_tri = (tri_view > 0) & (tri_view == own_tri)
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
    facing = torch.where(cosang >= MIN_FACING, cosang ** FACING_POW, torch.zeros_like(cosang))
    # 絵の色と alpha
    col = torch.nn.functional.grid_sample(img.permute(2, 0, 1)[None], grid, align_corners=False)[0]
    col = col.permute(1, 2, 0)
    inside = col[..., 3] > 0.5
    w = facing * visible.float() * inside.float() * covered.float()
    if th in (90, 270):
        w = w * SIDE_W
        if base_col is not None and os.environ.get('GATE', '1') == '1':
            has_base = base_w > 0.01
            diff = (col[..., :3] - base_col).norm(dim=-1)
            reject = has_base & (diff > MAXDIFF)
            w = torch.where(reject, torch.zeros_like(w), w)
    acc += col[..., :3] * w[..., None]; wsum += w
    if th == 180:                                        # 前後が終わった時点の基準色
        # ★基準色は【3D 空間で最も近い】前後から見えた面の色。UV の島で広げると
        #   兜の茶がフードに乗った（実測）。3D なら胴の側面の隣は胴の前後＝水色
        fb_known = wsum > float(os.environ.get('FILL_MIN_W', 0.05))
        base_col = nearest3d_fill(acc / wsum.clamp(min=1e-6)[..., None], fb_known, covered)
        base_w = covered.float()
    per_view[th] = float((w > 0.01).float().sum() / covered.float().sum())
    print(f'  {th:3d}° ({assign[th]}): 貼れたテクセル {per_view[th]:.1%}', flush=True)

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
json.dump({'assign': {str(k): v for k, v in assign.items()},
           'iou': {views[i][0]: float(ious[i, best[i]]) for i in range(4)},
           'per_view': {str(k): v for k, v in per_view.items()}},
          open(os.path.join(out_dir, 'report.json'), 'w'), indent=2)

# テクスチャ付き glb を書く（内部規約のまま）
from trimesh.visual.material import PBRMaterial
mat = PBRMaterial(baseColorTexture=Image.open(os.path.join(out_dir, 'albedo.png')),
                  metallicFactor=0.0, roughnessFactor=0.9)
out = trimesh.Trimesh(vertices=m.vertices, faces=F, process=False,
                      visual=trimesh.visual.TextureVisuals(uv=UV, material=mat))
out.export(os.path.join(out_dir, 'projected.glb'))
print('保存:', os.path.join(out_dir, 'projected.glb'), flush=True)
