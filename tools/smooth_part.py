# パーツの表面をならす（彫られた細部を落として、絵に任せる）。
#
# ★なぜ要るか（2026-08-30 実測）
#   clay（形だけ）で描画したところ、TRELLIS.2 は髪の筋・服の縫い目・ベルト・
#   顔の造作まで【全部を形として彫っている】ことが分かった。
#   元の絵は「滑らかな面に描かれた」フィギュアなので、彫られた分だけ光を拾って
#   筋状に見える。目が暗く落ち窪むのも同じ原因。
#   彫りを落としてテクスチャに任せる。
#
# 使いかた: python tools\smooth_part.py 入力.glb 出力.glb [回数] [強さ]
import sys
import numpy as np
import trimesh


ITERS, LAMBDA = 8, 0.5


def smooth(src, dst, iters=ITERS, lam=LAMBDA):
    """ならして保存し、動いた距離を返す。

    ★体には掛けないこと。背中の鍵穴が消える（2026-08-30 実測）。
    """
    m = trimesh.load(src, force='mesh', process=False)
    v0 = np.asarray(m.vertices, dtype=np.float64).copy()
    trimesh.smoothing.filter_laplacian(m, lamb=lam, iterations=iters,
                                       volume_constraint=True)
    v1 = np.asarray(m.vertices, dtype=np.float64)
    d = np.linalg.norm(v1 - v0, axis=1)
    size = (v0.max(0) - v0.min(0)).max()
    print(f'ならし: 回数 {iters} / 強さ {lam} / 動いた距離 平均 {d.mean():.5f}・'
          f'最大 {d.max():.5f}（全体の大きさ {size:.3f} に対し {d.mean()/size*100:.2f}%）',
          flush=True)
    m.export(dst)
    print('保存:', dst, flush=True)
    return {'mean': float(d.mean()), 'max': float(d.max()), 'size': float(size)}


def main():
    src, dst = sys.argv[1], sys.argv[2]
    iters = int(sys.argv[3]) if len(sys.argv) > 3 else ITERS
    lam = float(sys.argv[4]) if len(sys.argv) > 4 else LAMBDA
    smooth(src, dst, iters, lam)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit('使いかた: smooth_part.py 入力.glb 出力.glb [回数] [強さ]')
    main()
