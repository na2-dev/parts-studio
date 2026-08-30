# 入力の絵を、出来たモデルの表面に直接貼る（テクスチャ投影）。
#
# ★なぜ必要か（2026-08-23 実測で場所を特定）
#   色塗りAI（Hunyuan3D-Paint）は6方向の絵を【512×512で】描きます。これは上流の
#   モデルの学習解像度で、こちらでは変えられません（multiview_utils.py に
#   width=512, height=512 と直に書かれている）。全身が512に収まるので、
#   顔の目は【約15px】しかありません。そこから2048へ引き伸ばしているだけなので、
#   瞳孔もまぶたの線も戻りません。
#
#   実測した目の大きさ:
#     入力の絵（1376x2012） 約90x75px  ← ここに情報がある
#     AIが描く6方向の絵     約15px     ← ここで壊れる
#     焼けたテクスチャ(2048) 約60px    ← 容器は足りている
#
#   だから「512を上げる」ではなく【見えている面は512に頼らない】のが正解です。
#   AIのテクスチャを土台に、入力の絵が使える面だけ上から貼ります。
#
# ★品質が落ちないようにする仕掛け（ここが設計の中心）
#   投影の弱点は【位置ズレ】です。AIが作った形は元の絵と数px〜数十pxずれるので、
#   全面に貼ると目が二重に見えます。そこで貼る条件を絞ります。
#     1. その向きから【正面を向いている面】だけ（--minface）
#     2. 手前の面に【隠れていない】所だけ（自前のZバッファで判定）
#     3. 向きごとに、形のシルエットと絵のアルファの一致度を測り、
#        低い向きは【まるごと使わない】（--minshape）
#     4. 境界はぼかす（--feather）
#   合わない所には貼らないので、下限は元のテクスチャのままです。
#
# ★色ごと貼るか、模様だけ貼るか
#   --mode=color  … 色ごと貼る。元の絵の陰影も戻るので見た目がいちばん近い（既定）
#   --mode=detail … 細かい模様だけ移し、地の色はAIのまま。陰影の焼き付きを避けたいとき
#
# 使いかた:
#   venv\Scripts\python.exe tools\project_texture.py 入力.glb 出力.glb
#       --front=正面.png [--left=..] [--right=..] [--back=..]
#       [--mode=color|detail] [--minface=0.35] [--minshape=0.80]
#       [--feather=6] [--dump=<確認画像の保存先>]
import math
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import trimesh
from PIL import Image

import silhouette_iou as S          # 座標系・絵のそろえ方・一致度の測り方を共通にする

VIEWS = ('front', 'right', 'back', 'left')


def arg(name, default, cast=str):
    for a in sys.argv:
        if a.startswith(f'--{name}='):
            return cast(a.split('=', 1)[1])
    return default


def normalize(pts):
    """絵と同じ入れ方（背の高さ基準・中央）で [-1,1] の枠に収める。
    silhouette_iou.mesh_points と同じ規則にすること（ここがずれると全部ずれる）。"""
    lo, hi = pts.min(0), pts.max(0)
    out = pts - (lo + hi) / 2
    return out * ((2.0 * (1.0 - 2 * S.BORDER)) / (hi - lo)[1])


def project(pts, theta):
    """点を、その向きから見た (よこ, たて, 奥ゆき) に変換する。

    よこ・たては silhouette_iou.silhouette と同じ式。
    奥ゆきは【大きいほどカメラに近い】。正面が +Z なので front(θ=0) では z がそのまま。
    """
    c, s = math.cos(theta), math.sin(theta)
    u = pts[:, 0] * c - pts[:, 2] * s
    v = pts[:, 1]
    w = pts[:, 0] * s + pts[:, 2] * c
    return u, v, w


def to_pixel(u, v, size):
    col = (u + 1) / 2 * (size - 1)
    row = (1 - v) / 2 * (size - 1)
    return col, row


def raster_triangles(tri_xy, tri_val, size, reduce_max=True):
    """三角形を画素に塗る。reduce_max=True なら、重なりは大きい値が勝つ（Zバッファ）。

    三角形ごとに囲い枠だけを回す。60,000枚でも数秒で終わる。
    """
    buf = np.full((size, size), -np.inf if reduce_max else 0.0, dtype=np.float32)
    for i in range(len(tri_xy)):
        x0, y0 = tri_xy[i, 0]
        x1, y1 = tri_xy[i, 1]
        x2, y2 = tri_xy[i, 2]
        cmin = max(int(np.floor(min(x0, x1, x2))), 0)
        cmax = min(int(np.ceil(max(x0, x1, x2))), size - 1)
        rmin = max(int(np.floor(min(y0, y1, y2))), 0)
        rmax = min(int(np.ceil(max(y0, y1, y2))), size - 1)
        if cmax < cmin or rmax < rmin:
            continue
        xs = np.arange(cmin, cmax + 1, dtype=np.float32)
        ys = np.arange(rmin, rmax + 1, dtype=np.float32)
        gx, gy = np.meshgrid(xs, ys)
        d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(d) < 1e-12:
            continue
        a = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / d
        b = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / d
        c = 1 - a - b
        m = (a >= -1e-4) & (b >= -1e-4) & (c >= -1e-4)
        if not m.any():
            continue
        val = a * tri_val[i, 0] + b * tri_val[i, 1] + c * tri_val[i, 2]
        sub = buf[rmin:rmax + 1, cmin:cmax + 1]
        np.maximum(sub, np.where(m, val, -np.inf), out=sub)
    return buf


def box_blur(a, r):
    """軽いぼかし（境界をなじませる）。積分画像で速く。"""
    if r <= 0:
        return a
    pad = np.pad(a, r + 1, mode='edge')
    ii = pad.cumsum(0).cumsum(1)
    n = 2 * r + 1
    h, w = a.shape
    y0, x0 = 0, 0
    out = (ii[y0 + n:y0 + n + h, x0 + n:x0 + n + w]
           - ii[y0:y0 + h, x0 + n:x0 + n + w]
           - ii[y0 + n:y0 + n + h, x0:x0 + w]
           + ii[y0:y0 + h, x0:x0 + w])
    return out / (n * n)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    mode = arg('mode', 'color')
    minface = arg('minface', 0.35, float)      # 面がどれだけ正面を向いていれば貼るか
    minshape = arg('minshape', 0.80, float)    # その向きのシルエット一致度の下限
    feather = arg('feather', 6, int)
    depth_eps = arg('eps', 0.02, float)
    zsize = arg('zsize', 512, int)             # 隠れ判定に使う画面の大きさ
    dump = arg('dump', None)

    mesh = trimesh.load(src, force='mesh')
    uv = np.asarray(mesh.visual.uv, dtype=np.float64)
    base = mesh.visual.material.baseColorTexture
    if base is None:
        sys.exit('元のテクスチャがありません（色塗り済みのGLBを渡してください）')
    tex = np.asarray(base.convert('RGB'), dtype=np.float32)
    T = tex.shape[0]
    print(f'形: {os.path.basename(src)} / 面 {len(mesh.faces):,} / '
          f'テクスチャ {tex.shape[1]}x{T}', flush=True)

    masks = S.load_masks(zsize)                # 絵のそろえ方は本番と同じ（image_align）
    images = {}
    for v in VIEWS:
        p = arg(v, None)
        if p:
            images[v] = Image.open(p)
    images = S.align_views(images, border=S.BORDER, verbose=False) \
        if len(images) > 1 else images
    # ★ここを zsize（512）にしていたのが最初の実装ミスでした。
    #   512から拾ったら、色塗りAIと同じ解像度になってしまい、投影する意味が消えます。
    #   隠れ判定は512で足りますが、【色は元の解像度から拾います】。
    isize = arg('isize', 2048, int)
    rgb = {v: np.asarray(im.convert('RGB').resize((isize, isize), Image.LANCZOS),
                         dtype=np.float32) for v, im in images.items()}
    print(f'絵: そろえた後 {images[VIEWS[0]].size[0]}px → 色を拾う大きさ {isize}px / '
          f'隠れ判定 {zsize}px', flush=True)

    pts = normalize(np.asarray(mesh.vertices, dtype=np.float64))
    faces = mesh.faces
    fn = np.asarray(mesh.face_normals, dtype=np.float64)

    # 貼り付け先（テクスチャ空間）の三角形。UVは左下原点なので上下を返す
    tri_uv = np.stack([uv[faces[:, 0]], uv[faces[:, 1]], uv[faces[:, 2]]], axis=1)
    tri_px = np.empty_like(tri_uv)
    tri_px[..., 0] = tri_uv[..., 0] * (T - 1)
    tri_px[..., 1] = (1 - tri_uv[..., 1]) * (T - 1)

    acc = np.zeros((T, T, 3), dtype=np.float32)
    acc_w = np.zeros((T, T), dtype=np.float32)

    for v in VIEWS:
        if v not in rgb:
            continue
        theta = math.radians(S.ANGLE[v])
        # ---- その向きが信用できるかを先に測る（合わない向きは丸ごと使わない）
        if 'pv' not in dir():
            pv = normalize(np.asarray(
                trimesh.sample.sample_surface(mesh, 400_000, seed=1234)[0],
                dtype=np.float64))
        iou = S.iou(S.silhouette(pv, S.ANGLE[v], zsize, False), masks[v]) \
            if v in masks else 0.0
        if iou < minshape:
            print(f'  {v:5s} 形と絵の一致 {iou:.3f} < {minshape} → この向きは使いません',
                  flush=True)
            continue

        u, vv, w = project(pts, theta)

        # ★向きごとに【ずらしと拡大】を合わせ込む（2026-08-23 追加）
        #   シルエットの一致度が0.89あっても、中身（目や髪）は数十pxずれます。
        #   最初これをやらずに貼ったところ、目が二重に見える結果になりました。
        #   形のシルエットと絵のアルファが最もよく重なる (dx, dy, s) を探します。
        sil = S.silhouette(pv, S.ANGLE[v], zsize, False)
        best = (iou, 0.0, 0.0, 1.0)
        for sc in (0.94, 0.97, 1.0, 1.03, 1.06):
            for dx in (-0.06, -0.03, 0.0, 0.03, 0.06):
                for dy in (-0.06, -0.03, 0.0, 0.03, 0.06):
                    q = pv.copy()
                    q[:, 1] = q[:, 1] * sc + dy
                    qu, qv, _ = project(q, theta)
                    qu = qu * sc + dx
                    test = S.silhouette(np.stack([qu, qv, np.zeros_like(qu)], 1),
                                        0.0, zsize, False)
                    s_iou = S.iou(test, masks[v])
                    if s_iou > best[0]:
                        best = (s_iou, dx, dy, sc)
        fit_iou, dx, dy, sc = best
        print(f'  {v:5s} 位置合わせ: ずらし({dx:+.3f},{dy:+.3f}) 拡大{sc:.2f} → '
              f'一致 {iou:.3f} → {fit_iou:.3f}', flush=True)
        u = u * sc + dx
        vv = vv * sc + dy

        col, row = to_pixel(u, vv, zsize)
        scr = np.stack([col, row], axis=1)
        # 色を拾うのは大きい絵から。画面座標を色用の大きさに換算する
        colI, rowI = to_pixel(u, vv, isize)
        scrI = np.stack([colI, rowI], axis=1)

        # ---- 手前の面に隠れていないかを見るためのZバッファ
        tri_scr = np.stack([scr[faces[:, 0]], scr[faces[:, 1]], scr[faces[:, 2]]], axis=1)
        tri_w = np.stack([w[faces[:, 0]], w[faces[:, 1]], w[faces[:, 2]]], axis=1)
        zbuf = raster_triangles(tri_scr.astype(np.float32),
                                tri_w.astype(np.float32), zsize)

        # ---- その向きを向いている面だけを対象にする
        facing = fn[:, 0] * math.sin(theta) + fn[:, 2] * math.cos(theta)
        keep = np.nonzero(facing > minface)[0]
        print(f'  {v:5s} 形と絵の一致 {iou:.3f} / 正面を向いている面 '
              f'{len(keep):,} / {len(faces):,}', flush=True)

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
            gx, gy = np.meshgrid(np.arange(cmin, cmax + 1, dtype=np.float64),
                                 np.arange(rmin, rmax + 1, dtype=np.float64))
            x0, y0 = px[0]; x1, y1 = px[1]; x2, y2 = px[2]
            d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(d) < 1e-12:
                continue
            l0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / d
            l1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / d
            l2 = 1 - l0 - l1
            m = (l0 >= -1e-3) & (l1 >= -1e-3) & (l2 >= -1e-3)
            if not m.any():
                continue
            # この画素が形のどこかを求め、その点を絵に投影して色を拾う
            sp = l0[..., None] * scr[a] + l1[..., None] * scr[b] + l2[..., None] * scr[c]
            dw = l0 * w[a] + l1 * w[b] + l2 * w[c]
            sx = np.clip(np.round(sp[..., 0]).astype(np.int32), 0, zsize - 1)
            sy = np.clip(np.round(sp[..., 1]).astype(np.int32), 0, zsize - 1)
            spI = (l0[..., None] * scrI[a] + l1[..., None] * scrI[b]
                   + l2[..., None] * scrI[c])
            ix = np.clip(np.round(spI[..., 0]).astype(np.int32), 0, isize - 1)
            iy = np.clip(np.round(spI[..., 1]).astype(np.int32), 0, isize - 1)
            vis = m & (dw >= zbuf[sy, sx] - depth_eps) & (masks[v][sy, sx] if v in masks
                                                          else True)
            if not vis.any():
                continue
            wgt = float(min(1.0, (facing[fi] - minface) / (1.0 - minface))) ** 2
            sub_c = acc[rmin:rmax + 1, cmin:cmax + 1]
            sub_w = acc_w[rmin:rmax + 1, cmin:cmax + 1]
            sub_c[vis] += rgb[v][iy[vis], ix[vis]] * wgt
            sub_w[vis] += wgt
            pasted += int(vis.sum())
        print(f'        貼れた画素 {pasted:,}', flush=True)

    total = int((acc_w > 0).sum())
    if total == 0:
        print('★どの向きも使えませんでした。元のテクスチャのまま保存します', flush=True)
    else:
        print(f'投影できた画素 {total:,} / {T * T:,}（{total / T / T * 100:.1f}%）',
              flush=True)

    proj = np.where(acc_w[..., None] > 0, acc / np.maximum(acc_w, 1e-6)[..., None], tex)
    alpha = np.clip(acc_w, 0, 1)

    # ★画素ごとの関門（2026-08-23 追加）
    #   向き単位の関門だけでは足りませんでした。シルエットの一致が0.92でも、
    #   口や兜の飾りは【二重に見える】結果になりました。全体のずらしと拡大を
    #   合わせ込んでも、部分ごとの形の違いは残るためです。
    #
    #   そこで「AIのテクスチャと、貼ろうとしている絵が、その場所で
    #   同じ色を言っているか」を見ます。ぼかしてから比べるので、
    #   くっきりさの差ではなく【色の食い違い】だけが出ます。
    #   食い違いが大きい所は、境界がずれている＝二重像になる所なので貼りません。
    maxdiff = arg('maxdiff', 34.0, float)
    lo_p = np.stack([box_blur(proj[..., i], 4) for i in range(3)], -1)
    lo_t = np.stack([box_blur(tex[..., i], 4) for i in range(3)], -1)
    diff = np.abs(lo_p - lo_t).mean(-1)
    gate = np.clip(1.0 - (diff - maxdiff) / max(maxdiff, 1e-6), 0.0, 1.0)
    dropped = float((alpha > 0).sum() and ((alpha > 0) & (gate < 0.5)).sum()
                    / max((alpha > 0).sum(), 1))
    print(f'画素ごとの関門: 色の食い違いで {dropped * 100:.1f}% を貼らないことにしました'
          f'（しきい値 {maxdiff:.0f}/255）', flush=True)
    alpha = alpha * gate
    alpha = np.clip(box_blur(alpha, feather), 0, 1)      # 境界をなじませる

    if mode == 'detail':
        # 地の色はAIのまま、細かい模様だけ移す（陰影の焼き付きを避けたいとき）
        r = max(2, feather)
        proj = np.clip(tex + (proj - np.stack([box_blur(proj[..., i], r)
                                               for i in range(3)], -1)), 0, 255)

    out = tex * (1 - alpha[..., None]) + proj * alpha[..., None]
    out = np.clip(out, 0, 255).astype(np.uint8)

    if dump:
        os.makedirs(dump, exist_ok=True)
        Image.fromarray(out).save(os.path.join(dump, 'projected_color.png'))
        Image.fromarray((alpha * 255).astype(np.uint8)).save(
            os.path.join(dump, 'projected_mask.png'))
        print(f'確認画像: {dump}（projected_color.png / projected_mask.png）', flush=True)

    mesh.visual.material.baseColorTexture = Image.fromarray(out)
    mesh.export(dst)
    print(f'保存: {dst}（貼り方={mode}）', flush=True)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit('使いかた: project_texture.py 入力.glb 出力.glb --front=正面.png [...]')
    main()
