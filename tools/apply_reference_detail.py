# 元の絵の細部を、出来たモデルのテクスチャへ貼り直す（3d-studio の project_detail を呼ぶ）。
#
# ★なぜ必要か（3d-studio が 2026-08-24 に実測した数字）
#     入力の絵(1376x2012)   目の大きさ 約90x75px  ← ここに情報がある
#     AIが描く6方向の絵     約15px               ← ここで消える
#     焼けたテクスチャ(2048) 約60px              ← 容器は足りている
#   「容器ではなく中身が無い」ので、解像度を上げても戻らない。元の絵から持ってくる。
#
# ★座標系
#   project_detail は【右手系・Y上・正面が+Z】を前提にしている。
#   こちらの内部は Z上・正面が-Y なので、(x,y,z) -> (x, z, -y) で渡し、戻す。
#   なお左右どちらの絵がどの向きかは、向こうがシルエットIoUで自動対応づけする。
#
# 使いかた:
#   venv\Scripts\python.exe tools\apply_reference_detail.py 入力.glb 出力.glb ^
#       --front=正面.png --left=左.png --right=右.png --back=後ろ.png ^
#       [--mode=detail|color] [--top=0.0] [--bottom=1.0] [--dump=フォルダ]
#       [--partof=全身.glb] [--fixviews] [--up=y|z]
import os, sys
import numpy as np
import trimesh
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import project_detail as PD                                # noqa: E402
from project_detail import apply_detail, DEFAULTS          # noqa: E402
from project_texture import arg                            # noqa: E402

# ★視点の対応づけを固定する（--fixviews）
#   project_detail はシルエット IoU で絵とモデルの向きを自動対応づけする。
#   全身なら確実だが、【頭パーツはほぼ球形なので誤る】。実際に頭で走らせたら
#   「back（180°）← front の絵」となり、正面の顔を後頭部に貼ろうとした。
#   全身で測ったときの対応づけをそのまま使う。
FIXED = {'front': 'front', 'right': 'left', 'back': 'back', 'left': 'right'}


def _fixed_assign(pv, masks, zsize):
    out = {}
    for v, k in FIXED.items():
        if k in masks:
            out[v] = (k, 1.0)
    print('絵の割り当て（固定しました）:', flush=True)
    for v, (k, _) in out.items():
        print(f'  {v:5s} ← {k:5s} の絵', flush=True)
    return out


def to_yup(v):
    """内部(Z上・正面-Y) -> project_detail の規約(Y上・正面+Z)。"""
    return np.stack([v[:, 0], v[:, 2], -v[:, 1]], 1)


def to_zup(v):
    """逆変換。"""
    return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)


def detect_up(v):
    """一番長い軸を上とみなす。返り値は 'y' か 'z'。

    ★これは【全身にしか使えない】。パーツは背が低いので当たらない。
      実測（2026-08-31）: 頭 X=0.488 Y=0.433 Z=0.470、体 X=0.758 Y=0.287 Z=0.536。
      どちらも一番長いのは X で、Z 上なのに「Y 上」と判定してしまう。
      パーツを扱うときは切り出し元の上方向を up= で渡すこと。
    """
    ext = np.asarray(v).max(0) - np.asarray(v).min(0)
    return 'z' if int(np.argmax(ext)) == 2 else 'y'


def load_mesh_as_yup(path, up=None):
    """glb を読み、上方向を project_detail の規約（Y上）に揃える。

    ★上方向は書き出し経路で変わる（to_glb経由=Y上 / trimesh直=Z上）ので
      決め打ちしない。up に 'y' / 'z' を渡せばそれに従う。
    """
    m = trimesh.load(path, force='mesh')
    v = np.asarray(m.vertices, dtype=np.float64)
    if up is None:
        up = detect_up(v)
        how = 'と判定'
    else:
        how = 'と指定'
    if up not in ('y', 'z'):
        raise SystemExit(f"上方向は 'y' か 'z'。受け取った値: {up!r}")
    if up == 'z':
        m.vertices = to_yup(v)
        print(f'上方向: Z {how} -> Y上に変換', flush=True)
        return m, True
    print(f'上方向: Y {how} -> そのまま', flush=True)
    return m, False


def project(src, dst, images, partof=None, fixviews=False, up=None, **kw):
    """元の絵の細部を貼り直して保存する。

    images: {向き: パス または PIL.Image}。front は必須。
    partof: パーツを扱うとき、【切る前の全身】の glb を渡す。
        ★これを渡さないと、絵とメッシュがそれぞれ自分の高さで正規化されて
          対応が取れず、1 画素も貼れない（2026-08-30 実測。体 0%）。
          絵は切らずに全身のものをそのまま渡すこと。
    up: 'y' / 'z'。★パーツを扱うときは【切り出し元の上方向】を渡すこと。
        自動判定は一番長い軸を上とみなすので、背の低いパーツでは当たらない。
    fixviews: 視点の対応づけを固定する。
        ★頭のように上下に短いパーツは、シルエットの自動対応づけが
          正面を後ろに割り当てることがある（実測）。
    """
    imgs = {v: (Image.open(im) if isinstance(im, str) else im)
            for v, im in images.items() if im is not None}
    if 'front' not in imgs:
        raise SystemExit('正面の絵（front）は必須です')

    old_assign = PD.assign_views
    if fixviews:
        PD.assign_views = _fixed_assign
    try:
        mesh, converted = load_mesh_as_yup(src, up)
        if partof:
            # ★パーツと全身は同じ上方向でないと座標系がずれる。
            #   ずれると絵とメッシュが対応せず、貼れる画素が激減する
            #   （2026-08-31 実測: 頭 9.46% / 体 6.18%）
            fm, _ = load_mesh_as_yup(partof, up)
            kw['norm_ref'] = np.asarray(fm.vertices, dtype=np.float64)
            kw['fixfit'] = True
            print(f'パーツとして扱います（全身: {partof}）', flush=True)
        out = apply_detail(mesh, imgs, **kw)
    finally:
        PD.assign_views = old_assign        # ★同じプロセスで続けて呼ぶので必ず戻す
    if converted:
        out.vertices = to_zup(np.asarray(out.vertices, dtype=np.float64))
    out.export(dst)
    print(f'保存: {dst}', flush=True)
    return out


def main():
    src, dst = sys.argv[1], sys.argv[2]
    kw = {}
    for k, dv in DEFAULTS.items():
        got = arg(k, None)
        if got is not None:
            kw[k] = type(dv)(got) if dv is not None and not isinstance(dv, str) else got
    images = {v: arg(v, None) for v in ('front', 'left', 'right', 'back')}
    project(src, dst, images,
            partof=arg('partof', None),
            fixviews=arg('fixviews', None) is not None,
            up=arg('up', None), **kw)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit('使いかた: apply_reference_detail.py 入力.glb 出力.glb --front=正面.png ...')
    main()
