# 入力の絵の細部を、出来たモデルの表面に貼り直す（4方向・全身）。
#
# ★なぜ必要か（2026-08-24 実測）
#   色塗りAI（Hunyuan3D-Paint）は6方向の絵を【512×512で】描きます。全身が512に
#   収まるので、細かい模様はそこで消えます。実測した目の大きさ:
#
#     入力の絵（1376x2012）  約90x75px  ← ここに情報がある
#     AIが描く6方向の絵      約15px     ← ここで消える
#     焼けたテクスチャ(2048) 約60px     ← 容器は足りている
#
#   容器ではなく【中身】が無いので、テクスチャを大きくしても、くっきり補正を
#   強くしても戻りません。元の絵から持ってくるしかありません。
#   顔の目だけでなく、背中の鍵穴・破線のステッチ・ベルトの飾りなど、
#   線で描かれたものは全部この理由で消えています。
#
# ★前回（tools/project_texture.py・cf83090）が成立しなかった理由
#   全身に貼って、口のまわりと目の縁にゴーストが残りました。原因は位置合わせを
#   【向きごとに ずらし＋拡大 の1組】でしか行っていなかったことです。
#   シルエットの一致が 0.92 でも、部位ごとには数十pxずれます。1組の変換では
#   顔のズレと兜のズレを同時には吸収できません。
#
# ★この道具の違い：ブロックごとに位置を合わせる
#   全体で1組ではなく、画面を格子状のブロックに切って【ブロックごとに】
#   ずれを測ります（正規化相互相関＝NCC）。局所ではズレはほぼ平行移動なので、
#   これで吸収できます。さらに
#     ・一致度（NCC）が低いブロックは【使いません】
#     ・模様が無い所（のっぺりした布や肌）はそもそもブロックを立てません。
#       合わせようが無いので、そこには貼りません
#     ・AIのテクスチャと貼る絵の色が食い違う画素は貼らない（--maxdiff）
#   **合わない所には貼らないので、下限は「元のテクスチャのまま」です。**
#   裏を返すと、良くなるのは模様がある所だけです。
#
# ★GPUは使いません（環境依存を持ち込まないため）。
#
# ★確認画像（--dump）を必ず見てください。実機を回さずに良し悪しを判定できます。
#     compare_<向き>.png … 【元絵・貼る前・貼った後】の3段 ← これを見る
#     mask_<向き>.png    … 貼った濃さを画面に描いたもの。
#                          まだらが出たときは、まずこれを見る
#                          （黒い筋が走っていたら、そこは貼れていない）
#     match_<向き>.png   … ブロックごとのずれ（緑=使った/赤=捨てた）
#     texture.png        … 出来上がったテクスチャ
#
# 使いかた:
#   venv\Scripts\python.exe tools\project_detail.py 入力.glb 出力.glb
#       --front=正面.png [--left=左.png] [--right=右.png] [--back=後ろ.png]
#       [--mode=color|detail]             color=色ごと貼る（既定）／detail=模様だけ移す
#       [--top=0.0] [--bottom=1.0]        貼る帯（シルエットの上からの割合）
#       [--block=96] [--stride=24] [--search=40] [--minncc=0.35]
#       [--isize=2048] [--zsize=1280] [--minface=0.35] [--minshape=0.80]
#       [--feather=6] [--maxdiff=48] [--bleed=8]
#       [--dump=フォルダ]
import math
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import trimesh
from PIL import Image, ImageDraw

import silhouette_iou as S
from project_texture import arg, box_blur, normalize, project, to_pixel


# ---------------------------------------------------------------- 画面を作る

def rasterize(scr, depth, faces, size):
    """その向きから見た画面を作る。画素ごとに「どの面か」と「重心座標」を残す。

    奥ゆき depth は【大きいほどカメラに近い】（project_texture.project と同じ）。
    面ごとに囲い枠だけを回すので、6万面でも数秒です。
    """
    fid = np.full((size, size), -1, np.int32)
    bary = np.zeros((size, size, 3), np.float32)
    zbuf = np.full((size, size), -np.inf, np.float32)
    for i in range(len(faces)):
        a, b, c = faces[i]
        x0, y0 = scr[a]
        x1, y1 = scr[b]
        x2, y2 = scr[c]
        cmin = max(int(math.floor(min(x0, x1, x2))), 0)
        cmax = min(int(math.ceil(max(x0, x1, x2))), size - 1)
        rmin = max(int(math.floor(min(y0, y1, y2))), 0)
        rmax = min(int(math.ceil(max(y0, y1, y2))), size - 1)
        if cmax < cmin or rmax < rmin:
            continue
        d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(d) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(cmin, cmax + 1, dtype=np.float32),
                             np.arange(rmin, rmax + 1, dtype=np.float32))
        l0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / d
        l1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / d
        l2 = 1.0 - l0 - l1
        m = (l0 >= -1e-4) & (l1 >= -1e-4) & (l2 >= -1e-4)
        if not m.any():
            continue
        dep = l0 * depth[a] + l1 * depth[b] + l2 * depth[c]
        sub_z = zbuf[rmin:rmax + 1, cmin:cmax + 1]
        hit = m & (dep > sub_z)
        if not hit.any():
            continue
        sub_z[hit] = dep[hit]
        fid[rmin:rmax + 1, cmin:cmax + 1][hit] = i
        bary[rmin:rmax + 1, cmin:cmax + 1][hit] = np.stack([l0, l1, l2], -1)[hit]
    return fid, bary, zbuf


def sample(img, x, y):
    """双一次補間で色を拾う（x=列, y=行。どちらも実数）。"""
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0 = x.astype(np.int32)
    y0 = y.astype(np.int32)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    a = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    b = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return a * (1 - fy) + b * fy


def render(fid, bary, faces, uv, tex):
    """いまのテクスチャで、その向きから見た絵を描く（貼る前・貼った後の確認用）。"""
    size = fid.shape[0]
    out = np.full((size, size, 3), 235.0, np.float32)
    m = fid >= 0
    if not m.any():
        return out, m
    f = faces[fid[m]]
    b = bary[m]
    u = b[:, 0] * uv[f[:, 0], 0] + b[:, 1] * uv[f[:, 1], 0] + b[:, 2] * uv[f[:, 2], 0]
    v = b[:, 0] * uv[f[:, 0], 1] + b[:, 1] * uv[f[:, 1], 1] + b[:, 2] * uv[f[:, 2], 1]
    T = tex.shape[0]
    out[m] = sample(tex, u * (T - 1), (1 - v) * (T - 1))
    return out, m


# ------------------------------------------------- ブロックごとに ずれ を測る

def gray(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def highpass(g, r=8):
    """明るさの差ではなく【模様】で合わせるために、大きなムラを引く。

    AIのテクスチャと入力の絵は陰影も色調も違います。そのまま相関を取ると
    明るさの差に引きずられるので、ぼかしたものを引いて模様だけにします。
    """
    return g - box_blur(g, r)


def match_blocks(ref, tgt, valid, r0, r1, block, stride, search, minncc):
    """ref（AIの見た目）の各ブロックが、tgt（入力の絵）のどこにあるかを探す。

    戻り値: [(row, col, dy, dx, ncc, 採否), ...]
    """
    h = block // 2
    res = []
    rows = range(max(r0 + h, search + h), min(r1 - h, ref.shape[0] - h - search), stride)
    cols = range(search + h, ref.shape[1] - h - search, stride)
    for r in rows:
        for c in cols:
            if valid[r - h:r + h, c - h:c + h].mean() < 0.85:
                continue                      # 形からはみ出しているブロックは見ない
            t = ref[r - h:r + h, c - h:c + h]
            t = t - t.mean()
            tn = float(np.sqrt((t * t).sum()))
            # ★のっぺりした所は【ここで捨てます】。模様が無いと、どこへずらしても
            #   同じくらい合ってしまい、当てずっぽうの ずれ が出るためです。
            #   全身に広げても事故らないのは、この判定があるからです。
            if tn < 1e-3 or t.std() < 1.5:
                continue
            best = (-2.0, 0, 0)
            for dy in range(-search, search + 1, 2):
                for dx in range(-search, search + 1, 2):
                    p = tgt[r + dy - h:r + dy + h, c + dx - h:c + dx + h]
                    p = p - p.mean()
                    pn = float(np.sqrt((p * p).sum()))
                    if pn < 1e-3:
                        continue
                    v = float((t * p).sum()) / (tn * pn)
                    if v > best[0]:
                        best = (v, dy, dx)
            # 2px刻みで見つけた所のまわりを1px刻みで詰める
            _, by, bx = best
            for dy in range(by - 1, by + 2):
                for dx in range(bx - 1, bx + 2):
                    if abs(dy) > search or abs(dx) > search:
                        continue
                    p = tgt[r + dy - h:r + dy + h, c + dx - h:c + dx + h]
                    p = p - p.mean()
                    pn = float(np.sqrt((p * p).sum()))
                    if pn < 1e-3:
                        continue
                    v = float((t * p).sum()) / (tn * pn)
                    if v > best[0]:
                        best = (v, dy, dx)
            res.append((r, c, best[1], best[2], best[0], best[0] >= minncc))
    return res


def shift_field(blocks, size, sigma):
    """採用したブロックの ずれ を、画面全体になめらかに広げる。

    ブロックの中心にガウスの山を置いて重み付き平均を取ります。
    近くに採用ブロックが無い所は confidence が 0 になり、そこには貼りません。
    """
    dy = np.zeros((size, size), np.float32)
    dx = np.zeros((size, size), np.float32)
    wsum = np.zeros((size, size), np.float32)
    ys = np.arange(size, dtype=np.float32)[:, None]
    xs = np.arange(size, dtype=np.float32)[None, :]
    for (r, c, sy, sx, ncc, ok) in blocks:
        if not ok:
            continue
        g = np.exp(-(((ys - r) ** 2 + (xs - c) ** 2) / (2.0 * sigma * sigma)))
        w = g * float(ncc)
        wsum += w
        dy += w * sy
        dx += w * sx
    nz = wsum > 1e-6
    dy[nz] /= wsum[nz]
    dx[nz] /= wsum[nz]
    # 山の頂上（=ブロック中心）で1.0になるよう正規化した確からしさ
    conf = np.clip(wsum / max(float(wsum.max()), 1e-6) * 2.0, 0.0, 1.0)
    return dy, dx, conf


# ------------------------------------------------------------- UVのすき間対策

def uv_coverage(tri_px, T):
    """どの三角形にも属さないテクセル（＝UVの島と島のすき間）を求める。"""
    cov = np.zeros((T, T), bool)
    for i in range(len(tri_px)):
        px = tri_px[i]
        cmin = max(int(np.floor(px[:, 0].min())), 0)
        cmax = min(int(np.ceil(px[:, 0].max())), T - 1)
        rmin = max(int(np.floor(px[:, 1].min())), 0)
        rmax = min(int(np.ceil(px[:, 1].max())), T - 1)
        if cmax < cmin or rmax < rmin:
            continue
        x0, y0 = px[0]
        x1, y1 = px[1]
        x2, y2 = px[2]
        d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(d) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(cmin, cmax + 1, dtype=np.float64),
                             np.arange(rmin, rmax + 1, dtype=np.float64))
        l0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / d
        l1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / d
        l2 = 1 - l0 - l1
        cov[rmin:rmax + 1, cmin:cmax + 1] |= (l0 >= -1e-3) & (l1 >= -1e-3) & (l2 >= -1e-3)
    return cov


def bleed_edges(color, weight, n, allow):
    """貼った色を、となりの【すき間】テクセルへ n 回にじませる（UVパディング）。

    ★なぜ必要か（2026-08-24 実測で原因を特定）
      Hunyuan のUV展開は部位ひとつが細かい島に分かれていて、島と島の間には
      数テクセルのすき間があります。そこは元のテクスチャでは穴埋めで
      近くの色が入っていますが、貼るときは【どの三角形にも属さない】ので
      貼られません。すると画面に描いたときに双一次補間でそこが拾われ、
      顔じゅうに【元の色のままの細い筋】が走ります。
      （実測：貼った濃さを画面に描いた mask_*.png に、
        まさにその筋が黒い線として写っていました）

      allow で「すき間だけ」に限定します。他の島の中まで広げると、
      別の部位に違う色を塗ってしまいます。
    """
    col = color.copy()
    a = weight.copy()
    on = a > 1e-3
    for _ in range(n):
        nb_n = np.zeros(a.shape, np.float32)
        nb_c = np.zeros_like(col)
        nb_a = np.zeros_like(a)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            s = np.roll(on, (dy, dx), (0, 1)).astype(np.float32)
            nb_n += s
            nb_c += np.roll(col, (dy, dx), (0, 1)) * s[..., None]
            nb_a += np.roll(a, (dy, dx), (0, 1)) * s
        grow = (~on) & (nb_n > 0) & allow
        if not grow.any():
            break
        col[grow] = nb_c[grow] / nb_n[grow][..., None]
        a[grow] = nb_a[grow] / nb_n[grow]
        on |= grow
    return col, a


# ------------------------------------------------------------- 向きごとの処理

def assign_views(pv, masks, zsize):
    """どの絵が、どの向きから見た形に合うのかを【測って】決める。

    ★なぜ必要か（2026-08-25 実測で発覚）
      絵のファイル名の left / right が、形の左右と一致しているとは限りません。
      このデータでは【入れ替わっていました】。角度90°から見た形に合うのは
      `_cut_left.png` のほうです：
        名前どおり  … right 0.803 ／ left 0.827
        入れ替えると… right 0.867 ／ left 0.887
      決め打ちのまま貼ると、左右の絵を反対側に貼ってしまいます
      （実測でも左右のブロック採用率が 42/300・39/323 と極端に低く出ました）。

    ★yaw と左右反転の探索にしなかった理由
      最初そうしましたが、**front と back を壊しました**。silhouette_iou.py の
      冒頭にあるとおり、front/back のシルエットは「yaw=180＋反転」でも同じに
      なります。探索はそれを選び（左右は直るので平均が上がる）、結果として
      奥ゆきが裏返って、前から見ているのに背面が描かれる状態になりました。
      **「絵をどの向きに割り当てるか」は、形を回すことでは表せません。**
      素直に総当たりで測って、良い組から確定させます。
    """
    sil = {v: S.silhouette(pv, S.ANGLE[v], zsize, False) for v in S.VIEWS}
    pairs = sorted(((S.iou(sil[v], m), v, k) for v in S.VIEWS for k, m in masks.items()),
                   reverse=True)
    got_v, got_k, assign = set(), set(), {}
    for s, v, k in pairs:
        if v in got_v or k in got_k:
            continue
        assign[v] = (k, s)
        got_v.add(v)
        got_k.add(k)
    print('絵の割り当て（測って決めました）:', flush=True)
    for v in S.VIEWS:
        if v not in assign:
            continue
        k, s = assign[v]
        print(f'  {v:5s}（{int(S.ANGLE[v]):3d}°）← {k:5s} の絵 / 一致 {s:.3f}'
              f'{"   ★名前と違います" if k != v else ""}', flush=True)
    if len(masks) < 2:
        print('  ※絵が1枚だけなので、割り当ての正しさは確かめられていません', flush=True)
    return assign


def fit_view(pv, mask, view, zsize):
    """その向きの【全体の ずらし＋拡大】を粗く合わせる。

    これは大枠を合わせるだけで、これだけでは足りません（部位ごとのズレは
    このあとブロック照合で取ります）。
    """
    ang = S.ANGLE[view]
    iou0 = S.iou(S.silhouette(pv, ang, zsize, False), mask)
    best = (iou0, 0.0, 0.0, 1.0)
    for sc in (0.94, 0.97, 1.0, 1.03, 1.06):
        for ddx in (-0.06, -0.03, 0.0, 0.03, 0.06):
            for ddy in (-0.06, -0.03, 0.0, 0.03, 0.06):
                q = pv.copy()
                q[:, 1] = q[:, 1] * sc + ddy
                qu, qv, _ = project(q, math.radians(ang))
                qu = qu * sc + ddx
                t = S.silhouette(np.stack([qu, qv, np.zeros_like(qu)], 1),
                                 0.0, zsize, False)
                s_iou = S.iou(t, mask)
                if s_iou > best[0]:
                    best = (s_iou, ddx, ddy, sc)
    return (iou0,) + best


# ---------------------------------------------------------------------- 本体

DEFAULTS = dict(mode='color', top=0.0, bottom=1.0, block=96, stride=24, search=40,
                minncc=0.35, isize=2048, zsize=1280, minface=0.35, minshape=0.80,
                feather=6, maxdiff=48.0, eps=0.02, bleed=8, dump=None)


def apply_detail(mesh, imgs, **kw):
    """色塗り済みのメッシュに、入力の絵の細部を貼り直して返す。

    mesh … trimesh.Trimesh（UVと baseColorTexture があること）
    imgs … {'front': PIL, 'left': PIL, ...} 背景ぬき済みの【そろえる前】の絵
    そのほかは DEFAULTS を参照。app.py からも、CLI からも、ここを呼びます。

    ★名前を project にしないこと。project_texture から取り込んでいる
      project(pts, theta)（点を向きごとの画面座標に変換する）を上書きして、
      fit_view の中で壊れます。
    """
    o = dict(DEFAULTS, **kw)
    mode, top, bottom = o['mode'], o['top'], o['bottom']
    block, stride, search, minncc = o['block'], o['stride'], o['search'], o['minncc']
    isize, zsize = o['isize'], o['zsize']
    minface, minshape = o['minface'], o['minshape']
    feather, maxdiff, depth_eps, bleed = o['feather'], o['maxdiff'], o['eps'], o['bleed']
    dump = o['dump']
    t_all = time.time()

    uv = np.asarray(mesh.visual.uv, dtype=np.float64)
    base = getattr(mesh.visual.material, 'baseColorTexture', None)
    if base is None:
        raise ValueError('元のテクスチャがありません（色塗り済みのメッシュを渡してください）')
    tex = np.asarray(base.convert('RGB'), dtype=np.float32)
    T = tex.shape[0]
    faces = mesh.faces
    print(f'細部の貼り直し: 面 {len(faces):,} / '
          f'テクスチャ {tex.shape[1]}x{T} / 貼り方={mode}', flush=True)

    # ---- 入力の絵を本番と同じそろえ方（背の高さ基準・中央）にする
    #   ★そろえるのは【1回だけ】。silhouette_iou.load_masks を呼んでから
    #     別途 align_views をもう一度呼ぶと、align_views が安全側に倒れて
    #     何もしないで返す場合（アルファが無い／横長の絵が混ざっている）に、
    #     シルエットと色を拾う絵が違うそろえ方になり、貼る位置がずれます。
    if 'front' not in imgs:
        raise ValueError('正面の絵は必須です')
    imgs = S.align_views(imgs, border=S.BORDER, verbose=False)
    masks = {v: np.asarray(im.convert('RGBA').resize((zsize, zsize), Image.NEAREST))[
        ..., 3] > 8 for v, im in imgs.items()}
    img_z = {v: np.asarray(im.convert('RGB').resize((zsize, zsize), Image.LANCZOS),
                           dtype=np.float32) for v, im in imgs.items()}
    img_i = {v: np.asarray(im.convert('RGB').resize((isize, isize), Image.LANCZOS),
                           dtype=np.float32) for v, im in imgs.items()}
    print(f'絵: {"・".join(imgs)} / 色を拾う大きさ {isize}px / 画面 {zsize}px',
          flush=True)

    pts = normalize(np.asarray(mesh.vertices, dtype=np.float64))
    fn = np.asarray(mesh.face_normals, dtype=np.float64)
    vn = np.asarray(mesh.vertex_normals, dtype=np.float64)
    pv = normalize(np.asarray(
        trimesh.sample.sample_surface(mesh, 400_000, seed=1234)[0], dtype=np.float64))

    # ---- どの絵をどの向きに使うかを、測って決める
    assign = assign_views(pv, masks, zsize)

    tri_uv = np.stack([uv[faces[:, 0]], uv[faces[:, 1]], uv[faces[:, 2]]], axis=1)
    tri_px = np.empty_like(tri_uv)
    tri_px[..., 0] = tri_uv[..., 0] * (T - 1)
    tri_px[..., 1] = (1 - tri_uv[..., 1]) * (T - 1)

    acc = np.zeros((T, T, 3), np.float32)
    acc_w = np.zeros((T, T), np.float32)
    shots = {}                                  # 確認画像用（向き→描いた絵など）

    for view in [v for v in S.VIEWS if v in assign]:
        key = assign[view][0]                   # この向きに使う絵（名前と違うことがある）
        print(f'---- {view}（{int(S.ANGLE[view])}°・{key} の絵）', flush=True)
        iou0, fit_iou, gdx, gdy, gsc = fit_view(pv, masks[key], view, zsize)
        print(f'  全体の位置合わせ: ずらし({gdx:+.3f},{gdy:+.3f}) 拡大{gsc:.2f} → '
              f'一致 {iou0:.3f} → {fit_iou:.3f}', flush=True)
        # ★形と絵が合っていない向きは【丸ごと使いません】。
        #   合っていないまま貼ると、模様が別の場所に載ります。
        if fit_iou < minshape:
            print(f'  一致 {fit_iou:.3f} < {minshape} → この向きは使いません', flush=True)
            continue

        theta = math.radians(S.ANGLE[view])
        cs, sn = math.cos(theta), math.sin(theta)
        u, v, w = project(pts, theta)
        u = u * gsc + gdx
        v = v * gsc + gdy
        col, row = to_pixel(u, v, zsize)
        scr = np.stack([col, row], axis=1)
        colI, rowI = to_pixel(u, v, isize)
        scrI = np.stack([colI, rowI], axis=1)

        t0 = time.time()
        fid, bary, zbuf = rasterize(scr.astype(np.float32), w.astype(np.float32),
                                    faces, zsize)
        before, seen = render(fid, bary, faces, uv, tex)
        print(f'  描画: 写った画素 {int(seen.sum()):,}（{time.time() - t0:.1f}秒）',
              flush=True)

        ys = np.nonzero(seen.any(1))[0]
        if len(ys) == 0:
            print('  形が画面に写りませんでした', flush=True)
            continue
        y0, y1 = int(ys[0]), int(ys[-1])
        r0 = int(y0 + (y1 - y0) * top)
        r1 = int(y0 + (y1 - y0) * bottom)
        blk, std = block, stride
        # ★帯よりブロックが大きいと、置ける場所が1つも無くなって黙って何も貼りません
        if blk > (r1 - r0) * 0.8:
            blk = max(24, int((r1 - r0) * 0.8) // 2 * 2)
            std = max(8, min(std, blk // 3))
            print(f'  ブロックを {blk}px・刻み{std}px に下げました（帯が狭いため）',
                  flush=True)

        t0 = time.time()
        blocks = match_blocks(highpass(gray(before)), highpass(gray(img_z[key])),
                              seen, r0, r1, blk, std, search, minncc)
        used = [b for b in blocks if b[5]]
        if not used:
            print('  合うブロックがありませんでした → この向きは貼りません', flush=True)
            shots[view] = (key, fid, bary, before, seen, blocks, r0, r1)
            continue
        sy = np.array([b[2] for b in used], float)
        sx = np.array([b[3] for b in used], float)
        nc = np.array([b[4] for b in used], float)
        print(f'  ブロック照合: {len(blocks)}個中 {len(used)}個を採用 / '
              f'ずれ たて {sy.mean():+.1f}±{sy.std():.1f}px・'
              f'よこ {sx.mean():+.1f}±{sx.std():.1f}px / NCC 平均 {nc.mean():.2f}'
              f'（{time.time() - t0:.1f}秒）', flush=True)
        dyf, dxf, conf = shift_field(used, zsize, sigma=max(std, 1) * 1.2)

        ramp = np.zeros((zsize, zsize), np.float32)
        ramp[r0:r1] = 1.0
        ramp = np.clip(box_blur(ramp, max(feather * 2, 8)), 0, 1)
        wscr = ramp * conf

        # ---- テクスチャの画素ごとに、絵から色を拾って貼る
        t0 = time.time()
        facing = fn[:, 0] * sn + fn[:, 2] * cs        # その向きを向いている度合い
        vfacing = vn[:, 0] * sn + vn[:, 2] * cs
        frow = np.stack([row[faces[:, 0]], row[faces[:, 1]], row[faces[:, 2]]], 1)
        in_band = (frow.max(1) >= r0 - feather * 2) & (frow.min(1) <= r1 + feather * 2)
        keep = np.nonzero((facing > minface) & in_band)[0]

        pasted = 0
        for fi in keep:
            a, b, c = faces[fi]
            px = tri_px[fi]
            cmin = max(int(np.floor(px[:, 0].min())), 0)
            cmax = min(int(np.ceil(px[:, 0].max())), T - 1)
            rmin = max(int(np.floor(px[:, 1].min())), 0)
            rmax = min(int(np.ceil(px[:, 1].max())), T - 1)
            if cmax < cmin or rmax < rmin:
                continue
            x0, y0f = px[0]
            x1, y1f = px[1]
            x2, y2f = px[2]
            d = (y1f - y2f) * (x0 - x2) + (x2 - x1) * (y0f - y2f)
            if abs(d) < 1e-12:
                continue
            gx, gy = np.meshgrid(np.arange(cmin, cmax + 1, dtype=np.float64),
                                 np.arange(rmin, rmax + 1, dtype=np.float64))
            l0 = ((y1f - y2f) * (gx - x2) + (x2 - x1) * (gy - y2f)) / d
            l1 = ((y2f - y0f) * (gx - x2) + (x0 - x2) * (gy - y2f)) / d
            l2 = 1 - l0 - l1
            m = (l0 >= -1e-3) & (l1 >= -1e-3) & (l2 >= -1e-3)
            if not m.any():
                continue
            sp = l0[..., None] * scr[a] + l1[..., None] * scr[b] + l2[..., None] * scr[c]
            dw = l0 * w[a] + l1 * w[b] + l2 * w[c]
            sx_i = np.clip(np.round(sp[..., 0]).astype(np.int32), 0, zsize - 1)
            sy_i = np.clip(np.round(sp[..., 1]).astype(np.int32), 0, zsize - 1)
            # 手前の面に隠れていない／絵の中にある／貼る帯の中
            vis = m & (dw >= zbuf[sy_i, sx_i] - depth_eps) & masks[key][sy_i, sx_i]
            wt = wscr[sy_i, sx_i]
            vis &= wt > 0.01
            if not vis.any():
                continue
            # ★ブロックごとに測った ずれ を足してから、大きい絵から色を拾う
            spI = (l0[..., None] * scrI[a] + l1[..., None] * scrI[b]
                   + l2[..., None] * scrI[c])
            k = isize / zsize
            ix = spI[..., 0] + dxf[sy_i, sx_i] * k
            iy = spI[..., 1] + dyf[sy_i, sx_i] * k
            colr = sample(img_i[key], ix, iy)
            # ★向きの重みは【画素ごと】に出す（頂点法線を補間する）。
            #   面ごとに1つの値にすると、三角形の形をした濃淡のまだらが出ます。
            #   向きどうしの境目も、この重みでなめらかに混ざります。
            nz = l0 * vfacing[a] + l1 * vfacing[b] + l2 * vfacing[c]
            wgt = wt * np.clip((nz - minface) / 0.25, 0.0, 1.0)
            sub_c = acc[rmin:rmax + 1, cmin:cmax + 1]
            sub_w = acc_w[rmin:rmax + 1, cmin:cmax + 1]
            sub_c[vis] += colr[vis] * wgt[vis, None]
            sub_w[vis] += wgt[vis]
            pasted += int(vis.sum())
        print(f'  貼れた画素 {pasted:,}（{time.time() - t0:.1f}秒）', flush=True)
        shots[view] = (key, fid, bary, before, seen, blocks, r0, r1)

    total = int((acc_w > 0).sum())
    print(f'---- 合計: 貼れた画素 {total:,} / {T * T:,}'
          f'（{total / T / T * 100:.2f}%）', flush=True)

    if total == 0:
        print('★どの向きも貼れませんでした。元のテクスチャのまま保存します', flush=True)
        out = tex.astype(np.uint8)
        alpha = np.zeros((T, T), np.float32)
    else:
        proj = np.where(acc_w[..., None] > 0,
                        acc / np.maximum(acc_w, 1e-6)[..., None], tex)
        alpha = np.clip(acc_w, 0, 1)

        # ★画素ごとの関門（project_texture から引き継ぎ）
        #   ブロック照合を通っても、細部では合わない画素が残ります。
        #   ぼかしてから比べるので「くっきりさの差」ではなく【色の食い違い】だけが出ます。
        lo_p = np.stack([box_blur(proj[..., i], 4) for i in range(3)], -1)
        lo_t = np.stack([box_blur(tex[..., i], 4) for i in range(3)], -1)
        diff = np.abs(lo_p - lo_t).mean(-1)
        gate = np.clip(1.0 - (diff - maxdiff) / max(maxdiff, 1e-6), 0.0, 1.0)
        n_on = max(int((alpha > 0).sum()), 1)
        dropped = int(((alpha > 0) & (gate < 0.5)).sum()) / n_on
        print(f'画素ごとの関門: 色の食い違いで {dropped * 100:.1f}% を貼りません'
              f'（しきい値 {maxdiff:.0f}/255）', flush=True)
        alpha = np.clip(alpha * gate, 0, 1)

        if mode == 'detail':
            # 地の色はAIのまま、細かい模様だけ移す（元の絵の陰影を焼き付けたくないとき）
            r = max(2, feather)
            proj = np.clip(tex + (proj - np.stack(
                [box_blur(proj[..., i], r) for i in range(3)], -1)), 0, 255)

        # ★UVの島のすき間へ広げてから、ふちをぼかす（順番が逆だと筋が消えません）
        if bleed > 0:
            t0 = time.time()
            gap = ~uv_coverage(tri_px, T)
            proj, alpha = bleed_edges(proj, alpha, bleed, gap)
            print(f'UVのすき間へにじませました: {bleed}テクセル / '
                  f'すき間は全体の {gap.mean() * 100:.0f}%'
                  f'（{time.time() - t0:.1f}秒）', flush=True)
        alpha = np.clip(box_blur(alpha, feather), 0, 1)
        out = np.clip(tex * (1 - alpha[..., None]) + proj * alpha[..., None],
                      0, 255).astype(np.uint8)

    # ---- 確認画像
    if dump:
        os.makedirs(dump, exist_ok=True)
        Image.fromarray(out).save(os.path.join(dump, 'texture.png'))
        for view, (key, fid, bary, before, seen, blocks, r0, r1) in shots.items():
            after, _ = render(fid, bary, faces, uv, out.astype(np.float32))
            am, _ = render(fid, bary, faces, uv,
                           np.repeat((alpha * 255)[..., None], 3, -1).astype(np.float32))
            Image.fromarray(am.astype(np.uint8)).save(
                os.path.join(dump, f'mask_{view}.png'))

            used = [b for b in blocks if b[5]]
            mdy = int(round(np.mean([b[2] for b in used]))) if used else 0
            mdx = int(round(np.mean([b[3] for b in used]))) if used else 0
            xs = np.nonzero(seen.any(0))[0]
            cc0, cc1 = (int(xs[0]), int(xs[-1])) if len(xs) else (0, zsize - 1)
            cr0, cr1 = max(r0 - 8, 0), min(r1 + 8, zsize)
            src_tile = img_z[key][np.clip(np.arange(cr0, cr1) + mdy, 0, zsize - 1)][
                :, np.clip(np.arange(cc0, cc1) + mdx, 0, zsize - 1)]
            sc = max(1, min(6, int(1000 / max(cc1 - cc0, 1))))
            tiles = []
            for label, a_img in (('元絵', src_tile),
                                 ('貼る前', before[cr0:cr1, cc0:cc1]),
                                 ('貼った後', after[cr0:cr1, cc0:cc1])):
                t_im = Image.fromarray(np.clip(a_img, 0, 255).astype(np.uint8))
                t_im = t_im.resize((t_im.width * sc, t_im.height * sc), Image.LANCZOS)
                tag = Image.new('RGB', (52, 12), (255, 255, 255))
                ImageDraw.Draw(tag).text((1, 1), label, fill=(200, 0, 0))
                t_im.paste(tag.resize((52 * 3, 12 * 3), Image.NEAREST), (4, 4))
                tiles.append(t_im)
            cmp_im = Image.new('RGB', (tiles[0].width, tiles[0].height * 3 + 20),
                               (255, 255, 255))
            for i, t_im in enumerate(tiles):
                cmp_im.paste(t_im, (0, i * (t_im.height + 10)))
            cmp_im.save(os.path.join(dump, f'compare_{view}.png'))

            vis_im = Image.fromarray(before.astype(np.uint8)).convert('RGB')
            dr = ImageDraw.Draw(vis_im)
            for (r, c, sy, sx, ncc, ok) in blocks:
                col_ = (0, 200, 0) if ok else (220, 0, 0)
                dr.line([c, r, c + sx * 3, r + sy * 3], fill=col_, width=2)
                dr.ellipse([c - 3, r - 3, c + 3, r + 3], outline=col_, width=2)
            vis_im.save(os.path.join(dump, f'match_{view}.png'))
        print(f'確認画像: {dump}（compare_<向き>.png を見てください）', flush=True)

    mesh.visual.material.baseColorTexture = Image.fromarray(out)
    print(f'細部の貼り直し 完了（{time.time() - t_all:.1f}秒）', flush=True)
    return mesh


def main():
    src, dst = sys.argv[1], sys.argv[2]
    kw = {}
    for k, v in DEFAULTS.items():
        got = arg(k, None)
        if got is not None:
            kw[k] = type(v)(got) if v is not None and not isinstance(v, str) else got
    imgs = {}
    for v in S.VIEWS:
        p = arg(v, None)
        if p:
            imgs[v] = Image.open(p)
    if 'front' not in imgs:
        sys.exit('--front=正面.png は必須です')
    mesh = trimesh.load(src, force='mesh')
    print(f'形: {os.path.basename(src)}', flush=True)
    mesh = apply_detail(mesh, imgs, **kw)
    mesh.export(dst)
    print(f'保存: {dst}', flush=True)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit('使いかた: project_detail.py 入力.glb 出力.glb --front=正面.png '
                 '[--left=..] [--right=..] [--back=..] [--dump=フォルダ]')
    main()
