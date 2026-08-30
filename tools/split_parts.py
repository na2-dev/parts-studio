# メッシュを首の位置で「頭」と「体」に切り分ける（パーツ別にテクスチャを持たせるため）。
#
# ★なぜ切るのか（2026-08-30 実測）
#   全身を1枚の 2048 アトラスに詰めると、顔に回るテクセルが足りない。
#   3d-studio の出力は顔が1枚の大きなチャートに収まっていたが、こちらは
#   顔が2つに割れて小さかった。頭を独立させれば、頭だけで 2048 を使える。
#
# ★頭部だけで「形」を作るのは失敗している（docs/measurements/2026-08-30-face-quality.md）。
#   4視点の対応づけができないため。ここでやるのは【形は全身のまま作り、
#   出来たメッシュを切る】ことなので、その問題は起きない。
#   塗りは「1枚の絵＋形」で条件づけるので、頭だけでも成立する。
#
# 首の見つけ方: 上半分で、水平断面の広がり（XYの外接矩形の対角）が最小になる高さ。
#
# 使いかた:
#   python tools\split_parts.py 入力.glb 頭.glb 体.glb [--margin=0.01] [--up=z]
import sys
import numpy as np
import trimesh


def find_neck(v, lo=0.45, hi=0.80, bins=120):
    """上半分で断面の広がりが最小になる高さ（＝首）を返す。v は Z 上。"""
    z = v[:, 2]
    z0, z1 = z.min(), z.max()
    span = z1 - z0
    zs = np.linspace(z0 + span * lo, z0 + span * hi, bins)
    best, best_w = None, np.inf
    for zc in zs:
        band = np.abs(z - zc) < span * 0.01
        if band.sum() < 20:
            continue
        p = v[band]
        w = np.hypot(p[:, 0].max() - p[:, 0].min(), p[:, 1].max() - p[:, 1].min())
        if w < best_w:
            best_w, best = w, zc
    if best is None:
        # ★黙って高さ45%を返さない。頂点が薄い題材だと、胴の真ん中で切った
        #   「頭」ができてしまい、そのまま 47 秒かけて塗ることになる
        raise SystemExit(
            f'首が見つかりません（高さ {lo:.0%}〜{hi:.0%} のどの断面にも頂点が '
            f'20 個ありませんでした。頂点 {len(v):,}）。'
            '入力が薄すぎないか確かめてください。')
    return best, best_w


UP_AXIS = {'x': 0, 'y': 1, 'z': 2}
MIN_SHARE = 0.02          # 頭・体それぞれが全体の面数に占める最低の割合


def split(path, head_path, body_path, margin=0.01, up='z'):
    """首で切って頭と体を書き出す。

    up: 上方向。★測って決めない。「一番長い軸が上」は腕を広げた題材で
        外れる（横幅が背丈を超えると、腕に沿って首を探して
        「頭＝片腕と胴の右半分」になる）。呼ぶ側が知っている値を渡すこと。
    """
    if up not in UP_AXIS:
        raise SystemExit(f'上方向は {sorted(UP_AXIS)} のいずれか。受け取った値: {up!r}')
    if not -0.5 < margin < 0.5:
        raise SystemExit(f'--margin は -0.5〜0.5 にすること。受け取った値: {margin}')
    m = trimesh.load(path, force='mesh', process=False)
    v = np.asarray(m.vertices, dtype=np.float64)
    zaxis = UP_AXIS[up]
    if zaxis != 2:                     # 上方向が Z でなければ入れ替えて判定する
        order = [0, 1, 2]; order[2], order[zaxis] = order[zaxis], order[2]
        vz = v[:, order]
    else:
        vz = v
    f = np.asarray(m.faces)
    neck, w = find_neck(vz)
    span = vz[:, 2].max() - vz[:, 2].min()
    cut = neck + span * margin
    fz = vz[f].mean(axis=1)[:, 2]
    is_head = fz > cut
    print(f'上方向 {up.upper()} / 首の高さ {neck:.3f}（断面の広がり {w:.3f}）/ '
          f'切る高さ {cut:.3f}', flush=True)
    print(f'頭 {is_head.sum():,} 面 / 体 {(~is_head).sum():,} 面', flush=True)
    # ★どちらかがほぼ空でないか見る。空のまま進むと 47 秒かけて空を塗る
    for n, name in ((int(is_head.sum()), '頭'), (int((~is_head).sum()), '体')):
        if n < len(f) * MIN_SHARE:
            raise SystemExit(
                f'{name}の面が少なすぎます（{n:,} / 全体 {len(f):,} = '
                f'{n / len(f):.1%}、下限 {MIN_SHARE:.0%}）。'
                f'切る高さ {cut:.3f} が外れています。--margin を見直してください。')

    for sel, out, name in ((is_head, head_path, '頭'), (~is_head, body_path, '体')):
        fs = f[sel]
        uniq, inv = np.unique(fs.reshape(-1), return_inverse=True)
        sub = trimesh.Trimesh(vertices=v[uniq], faces=inv.reshape(-1, 3), process=False)
        sub.export(out)
        print(f'  {name}: 頂点 {len(uniq):,} 面 {len(fs):,} -> {out}', flush=True)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        sys.exit('使いかた: split_parts.py 入力.glb 頭.glb 体.glb '
                 '[--margin=0.01] [--up=z]')
    mg, up = 0.01, 'z'
    for a in sys.argv[4:]:
        if a.startswith('--margin='):
            mg = float(a.split('=', 1)[1])
        elif a.startswith('--up='):
            up = a.split('=', 1)[1]
    split(sys.argv[1], sys.argv[2], sys.argv[3], mg, up)
