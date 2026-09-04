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


def boundary_vertices(mesh):
    """開いた縁（1つの面にしか属さない辺）の頂点番号を返す。

    ★首で切ったパーツには切り口という開いた縁がある。
    """
    e = np.sort(np.asarray(mesh.faces)[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    uniq, cnt = np.unique(e, axis=0, return_counts=True)
    return np.unique(uniq[cnt == 1])


def smooth(src, dst, iters=ITERS, lam=LAMBDA, pin_boundary=True):
    """ならして保存し、動いた距離を返す。

    ★体には掛けないこと。背中の鍵穴が消える（2026-08-30 実測）。

    ★切り口（開いた縁）は動かさない（pin_boundary）。2026-08-31 実測で、
      ならすと首の切り口が z +0.00803・平均半径 -3%・中心が Y に +0.039 動いた。
      体はならさないので、そのままだと【頭と体の切り口が合わなくなる】。
      実際、頭の下端 0.03712 が体の上端 0.03642 を上回り、隙間ができていた。
    """
    m = trimesh.load(src, force='mesh', process=False)
    v0 = np.asarray(m.vertices, dtype=np.float64).copy()
    pinned = boundary_vertices(m) if pin_boundary else np.array([], dtype=int)
    trimesh.smoothing.filter_laplacian(m, lamb=lam, iterations=iters,
                                       volume_constraint=True)
    if len(pinned):
        v = np.asarray(m.vertices, dtype=np.float64)
        v[pinned] = v0[pinned]                 # ★切り口を元の位置へ戻す
        m.vertices = v
    v1 = np.asarray(m.vertices, dtype=np.float64)
    d = np.linalg.norm(v1 - v0, axis=1)
    size = (v0.max(0) - v0.min(0)).max()
    print(f'ならし: 回数 {iters} / 強さ {lam} / 切り口を固定 {len(pinned)}点 / '
          f'動いた距離 平均 {d.mean():.5f}・最大 {d.max():.5f}'
          f'（全体の大きさ {size:.3f} に対し {d.mean()/size*100:.2f}%）', flush=True)
    m.export(dst)
    print('保存:', dst, flush=True)
    return {'mean': float(d.mean()), 'max': float(d.max()), 'size': float(size),
            'pinned': int(len(pinned))}


def main():
    src, dst = sys.argv[1], sys.argv[2]
    iters = int(sys.argv[3]) if len(sys.argv) > 3 else ITERS
    lam = float(sys.argv[4]) if len(sys.argv) > 4 else LAMBDA
    smooth(src, dst, iters, lam)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit('使いかた: smooth_part.py 入力.glb 出力.glb [回数] [強さ]')
    main()
