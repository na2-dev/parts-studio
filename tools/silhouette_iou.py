# 4方向シルエットIoU：出来た形が「左右と後ろから見ても入力の絵と合っているか」を数字で出す。
#
# ★★この指標だけで形の良し悪しを決めてはいけません（2026-08-23 に痛い目を見ました）
#   シルエットIoU は【くぼみを一切見ません】。影に写らないからです。
#   この数字で「0.753 → 0.908 に良くなった」と判断して切り替えた実物は、
#   頭が一体化した巨大なつるつるの塊でした（くぼみが全部埋まっていた）。
#   自分の手法に都合よく反応する指標で、自分の手法を評価してしまった例です。
#   詳しくは docs/廃止メモ_形づくり2.2_Omni.md。
#   **形を変えたときの良し悪しは tools/compare_shots.py で実物を並べて撮って決めます。**
#   ★compare_shots.py は 3d-studio 側の道具で、parts-studio には未移植です。
#   ここで測るのは「シルエットが合っているか」だけ、と割り切って使ってください。
#
# ★なぜ必要か（2026-08-22）
#   2.1 は正面1枚しか使わないので、左右と後ろの形は想像です。
#   その「合っていない度合い」を、目で見た感想ではなく数字にするために作りました。
#   入力の絵は背景ぬき済み＝そのアルファが【正解のシルエット】なので、
#   出来た形を同じ向きから見たシルエットと重ね合わせれば、そのまま指標になります。
#     IoU = 重なった面積 ÷ どちらかに入っている面積（1.0で完全一致）
#
# ★どの数字を見るのか（2026-08-22 実測して確定）
#   【後ろのIoUは、後ろの形の正しさを測っていません】。後ろから見た絵はおくゆき方向に
#   つぶした影なので、写るのは「よこ×たて」＝正面とほぼ同じ情報です。実測すると、
#   おくゆきを0.7倍・0.5倍にしても、背中のリュックを消しても、背中を平らにつぶしても、
#   後ろのIoUは 0.955→0.956 でまったく動きませんでした。
#
#   おくゆき方向の狂いが写るのは【左と右（横顔）のIoUだけ】です。同じ実測での落ち幅：
#     同じ形（上限）0.95 ／ おくゆき0.7倍 0.70 ／ リュックを消す 0.85
#     背中を平らに 0.43 ／ 後ろを前のコピーに 0.19
#   つまり「正面しか使っていない」度合いは、左右のIoUで判定します。
#
# ★向きの合わせ方（ここが要）
#   入力の絵は image_align.align_views で「背の高さ基準・中央・同じ枠」にそろえます
#   （操作画面の本番と同じ関数を使う）。形の方も同じ入れ方で枠に収めるので、
#   両者は比べられる状態になります。
#
#   そのうえで【AIが作った形の正面がどちらを向いているか】は分かりません。
#   そこで yaw（Y軸まわり0/90/180/270度）と左右反転の8通りを全部試し、
#   いちばん合う組み合わせを報告します。当てずっぽうを無くすための仕掛けで、
#   既にある 2.1 の出力で1回走らせれば、以後その値を固定で使えます。
#
# ★シルエットの作り方
#   GPUのラスタライザは使いません（環境依存を持ち込まないため）。
#   面から点をたくさん拾って画素に落とし、内側の穴を埋めます。
#   点の数が足りないと隙間が出るので、既定は200万点です。
#
# 使いかた:
#   venv\Scripts\python.exe tools\silhouette_iou.py <形.glb> --front=正面.png
#       [--left=左.png] [--right=右.png] [--back=後ろ.png]
#       [--size=256] [--points=2000000] [--seed=1234]
#       [--yaw=auto|0|90|180|270] [--mirror=auto|0|1] [--dump=<保存先フォルダ>]
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import numpy as np
import trimesh
from PIL import Image

from image_align import align_views

VIEWS = ('front', 'right', 'back', 'left')     # 右手系・Y上・正面が+Z のときの並び
ANGLE = {'front': 0.0, 'right': 90.0, 'back': 180.0, 'left': 270.0}
BORDER = 0.10                                   # align_views の既定と合わせる


def arg(name, default, cast=str):
    for a in sys.argv:
        if a.startswith(f'--{name}='):
            return cast(a.split('=', 1)[1])
    return default


def load_masks(size):
    """入力の絵を読み、本番と同じそろえ方をしてから、白黒のシルエットにする。"""
    images = {}
    for v in VIEWS:
        p = arg(v, None)
        if p:
            images[v] = Image.open(p)
    if 'front' not in images:
        sys.exit('--front=正面.png は必須です')
    images = align_views(images, border=BORDER, verbose=False)
    masks = {}
    for v, im in images.items():
        a = np.asarray(im.convert('RGBA').resize((size, size), Image.NEAREST))
        masks[v] = a[..., 3] > 8
    return masks


def mesh_points(path, count, seed):
    """形から点を拾い、絵と同じ入れ方（背の高さ基準・中央）で枠に収める。"""
    m = trimesh.load(path, force='mesh')
    pts = trimesh.sample.sample_surface(m, count, seed=seed)[0]
    pts = np.asarray(pts, dtype=np.float64)
    lo, hi = pts.min(0), pts.max(0)
    pts -= (lo + hi) / 2                        # 中央へ
    height = (hi - lo)[1]                       # ★背の高さ（Y）を基準にする。絵と同じ考え方
    if height <= 0:
        sys.exit('形の高さが0です')
    pts *= (2.0 * (1.0 - 2 * BORDER)) / height  # 枠[-1,1]の80%に収める
    return pts


def silhouette(pts, angle_deg, size, mirror=False):
    """点群を指定の向きから見たシルエット（白黒）にする。

    ★2026-08-22 修正：反転は【形の側（Xを反転）】に掛けます。
      最初は投影したあとの u の符号を返していましたが、それでは
      「180度回してから反転」＝「そのまま」と数学的に同じものになり、
      8通りが実質4通りに縮退します。つまり【左右の取り違えを検出できません】。
      いちばん検出したいのがそれなので、ここは形の側で反転させます。
      （検算：左右反転させた形を入れて、反転=有 が選ばれることを確認済み）
    """
    x = -pts[:, 0] if mirror else pts[:, 0]
    t = np.radians(angle_deg)
    u = x * np.cos(t) - pts[:, 2] * np.sin(t)
    v = pts[:, 1]
    col = np.clip(((u + 1) / 2 * (size - 1)).astype(np.int32), 0, size - 1)
    row = np.clip(((1 - v) / 2 * (size - 1)).astype(np.int32), 0, size - 1)
    img = np.zeros((size, size), dtype=bool)
    img[row, col] = True
    return fill_holes(img)


def fill_holes(img):
    """内側の穴を埋める。点で描いているので隙間が残るのを塞ぐ。

    scipy があればそれを使い、無ければ「上下左右から届く外側」を塗って
    残りを内側とみなす簡易版で代用する（依存を増やさないため）。
    """
    try:
        from scipy.ndimage import binary_closing, binary_fill_holes
        img = binary_closing(img, np.ones((3, 3), bool))
        return binary_fill_holes(img)
    except ImportError:
        out = img.copy()
        for axis in (0, 1):
            fwd = np.maximum.accumulate(img, axis=axis)
            bwd = np.flip(np.maximum.accumulate(np.flip(img, axis), axis=axis), axis)
            out |= fwd & bwd            # その行/列で「両側に中身がある」なら内側
        return out


def iou(a, b):
    inter = np.count_nonzero(a & b)
    union = np.count_nonzero(a | b)
    return inter / union if union else 0.0


def main():
    path = sys.argv[1]
    size = arg('size', 256, int)
    count = arg('points', 2_000_000, int)
    seed = arg('seed', 1234, int)
    yaw_opt = arg('yaw', 'auto')
    mir_opt = arg('mirror', 'auto')
    dump = arg('dump', None)

    masks = load_masks(size)
    pts = mesh_points(path, count, seed)
    print(f'形: {os.path.basename(path)} / 点 {len(pts)}個 / '
          f'絵: {"・".join(masks)} / {size}x{size}px', flush=True)

    yaws = [0.0, 90.0, 180.0, 270.0] if yaw_opt == 'auto' else [float(yaw_opt)]
    mirrors = [False, True] if mir_opt == 'auto' else [mir_opt == '1']

    best = None
    for yaw in yaws:
        for mir in mirrors:
            per = {v: iou(silhouette(pts, ANGLE[v] + yaw, size, mir), m)
                   for v, m in masks.items()}
            mean = sum(per.values()) / len(per)
            if len(yaws) > 1 or len(mirrors) > 1:
                print(f'  yaw={int(yaw):3d}° 反転={"有" if mir else "無"} → '
                      f'平均 {mean:.3f}  （'
                      + ' / '.join(f'{v} {per[v]:.3f}' for v in masks) + '）', flush=True)
            if best is None or mean > best[0]:
                best = (mean, yaw, mir, per)

    mean, yaw, mir, per = best
    print('---- 結果 ----', flush=True)
    print(f'向き: yaw={int(yaw)}° / 左右反転={"有" if mir else "無"}', flush=True)
    for v in masks:
        print(f'  {v:5s} IoU {per[v]:.3f}', flush=True)
    print(f'  平均  IoU {mean:.3f}', flush=True)
    # ★見るべきは左右（横顔）だけ。後ろは正面とほぼ同じ情報しか持たないので混ぜない。
    side = [per[v] for v in ('left', 'right') if v in masks]
    if side:
        print(f'  正面 {per["front"]:.3f} ／ 横顔（左右）の平均 {sum(side)/len(side):.3f}'
              f'  ← おくゆきの正しさはこちらだけが見ている', flush=True)
        print('     目安: 同じ形なら0.95 / リュック1個ぶんの欠けで0.85 / '
              '背中が平らで0.43 / 後ろが前のコピーで0.19', flush=True)

    if dump:
        os.makedirs(dump, exist_ok=True)
        for v, m in masks.items():
            s = silhouette(pts, ANGLE[v] + yaw, size, mir)
            # 赤=絵だけ / 緑=形だけ / 白=一致
            rgb = np.zeros((size, size, 3), dtype=np.uint8)
            rgb[..., 0] = np.where(m & ~s, 255, 0) + np.where(m & s, 255, 0)
            rgb[..., 1] = np.where(s & ~m, 255, 0) + np.where(m & s, 255, 0)
            rgb[..., 2] = np.where(m & s, 255, 0)
            Image.fromarray(rgb).save(os.path.join(dump, f'iou_{v}.png'))
        print(f'重ね合わせ画像: {dump}（赤=絵だけ・緑=形だけ・白=一致）', flush=True)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('使いかた: silhouette_iou.py <形.glb> --front=正面.png [--left=..] [--right=..] [--back=..]')
    main()
