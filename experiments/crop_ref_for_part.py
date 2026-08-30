# パーツに対応する範囲で元の絵を切り出す。
#
# ★なぜ要るか（2026-08-30 実測）
#   体パーツに元絵を貼ろうとして、手で「首の高さ」で切った絵を渡したら、
#   シルエットの一致が 0.39〜0.72 にしかならず【1画素も貼れなかった】。
#   絵の首とメッシュの首が同じ高さにならないため。
#
#   ★パーツを各方向へ投影した外接矩形で切れば、必ず合う。
#     フードや兜のような複雑な形でも同じ仕組みで通る。
#
# 正規化は project_detail と同じ規則（背の高さ基準・中央・BORDER=0.10）にすること。
# ここがずれると全部ずれる。
#
# 使いかた:
#   python experiments\crop_ref_for_part.py 全身.glb パーツ.glb 出力フォルダ ^
#       --front=正面.png --left=左.png --right=右.png --back=後ろ.png [--margin=0.06]
import os, sys
import numpy as np
import trimesh
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
# ★experiments/ へ移したので、依存している道具は tools/ 側にある。
#   HERE は足さない。ここに tools/ と同名のモジュールを置いたとき、
#   本線側の import が黙ってこちらを掴むため。
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import silhouette_iou as S                                  # noqa: E402
from project_texture import arg                             # noqa: E402


def to_yup(v):
    return np.stack([v[:, 0], v[:, 2], -v[:, 1]], 1)


def load_yup(path):
    m = trimesh.load(path, force='mesh', process=False)
    v = np.asarray(m.vertices, dtype=np.float64)
    ext = v.max(0) - v.min(0)
    if int(np.argmax(ext)) == 2:      # Z 上なら Y 上へ
        v = to_yup(v)
    return v


def main():
    full_p, part_p, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    margin = float(arg('margin', 0.06))
    os.makedirs(outdir, exist_ok=True)

    full = load_yup(full_p)
    part = load_yup(part_p)
    # ★正規化は【全身】で決める。パーツ単体で正規化すると絵と対応しない。
    lo, hi = full.min(0), full.max(0)
    ctr = (lo + hi) / 2
    scale = (2.0 * (1.0 - 2 * S.BORDER)) / (hi - lo)[1]
    part = (part - ctr) * scale

    imgs = {}
    for v in ('front', 'left', 'right', 'back'):
        p = arg(v, None)
        if p:
            imgs[v] = Image.open(p)
    imgs = S.align_views(imgs, border=S.BORDER, verbose=False)

    # ★矩形で切るだけでは足りない（2026-08-30 実測）
    #   正面の「体」を矩形で切ると、フードの垂れ下がりが写り込む。メッシュの体には
    #   それが無いのでシルエットが合わず、一致 0.64 で見送られ【1画素も貼れない】。
    #   パーツのシルエットで元絵をマスクしてから切る。
    from PIL import ImageFilter
    for view, img in imgs.items():
        ang = S.ANGLE[view]
        W, H = img.size
        size = max(W, H)
        sil = S.silhouette(part, ang, size, False)          # パーツを同じ向きから見た影
        m = Image.fromarray((np.asarray(sil) > 0).astype(np.uint8) * 255).resize((size, size),
                                                                                Image.NEAREST)
        m = m.filter(ImageFilter.MaxFilter(9))              # 少し太らせて縁を残す
        m = m.crop((0, 0, W, H))
        rgba = img.convert('RGBA')
        a = np.asarray(rgba)[..., 3]
        keep = np.minimum(a, np.asarray(m))
        arr = np.asarray(rgba).copy(); arr[..., 3] = keep
        masked = Image.fromarray(arr)
        nz = np.argwhere(keep > 8)
        if nz.size == 0:
            print(f'{view:6s} 角度{ang:4.0f}° -> パーツが写りません（飛ばします）', flush=True)
            continue
        r0, c0 = nz.min(0); r1, c1 = nz.max(0)
        mw = int((c1 - c0) * margin); mh = int((r1 - r0) * margin)
        box = (max(0, c0 - mw), max(0, r0 - mh), min(W, c1 + mw), min(H, r1 + mh))
        masked.crop(box).save(os.path.join(outdir, f'{view}.png'))
        print(f'{view:6s} 角度{ang:4.0f}° -> {box[2]-box[0]}x{box[3]-box[1]} '
              f'（マスク後の画素 {int((keep>8).sum()):,}）', flush=True)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit('使いかた: crop_ref_for_part.py 全身.glb パーツ.glb 出力フォルダ --front=... ')
    main()
