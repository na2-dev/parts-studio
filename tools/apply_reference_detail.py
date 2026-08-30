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


def load_mesh_as_yup(path):
    """glb を読み、上方向を実測して project_detail の規約に揃える。

    ★上方向は書き出し経路で変わる（to_glb経由=Y上 / trimesh直=Z上）。決め打ちしない。
    """
    m = trimesh.load(path, force='mesh')
    v = np.asarray(m.vertices, dtype=np.float64)
    ext = v.max(0) - v.min(0)
    up = int(np.argmax(ext))
    if up == 2:
        m.vertices = to_yup(v)
        print(f'上方向: Z と判定 -> Y上に変換', flush=True)
        return m, True
    print(f'上方向: Y と判定 -> そのまま', flush=True)
    return m, False


def main():
    src, dst = sys.argv[1], sys.argv[2]
    kw = {}
    for k, dv in DEFAULTS.items():
        got = arg(k, None)
        if got is not None:
            kw[k] = type(dv)(got) if dv is not None and not isinstance(dv, str) else got
    imgs = {}
    for v in ('front', 'left', 'right', 'back'):
        p = arg(v, None)
        if p:
            imgs[v] = Image.open(p)
    if 'front' not in imgs:
        sys.exit('--front=正面.png は必須です')

    if arg('fixviews', None) is not None:
        PD.assign_views = _fixed_assign
    mesh, converted = load_mesh_as_yup(src)
    out = apply_detail(mesh, imgs, **kw)
    if converted:
        out.vertices = to_zup(np.asarray(out.vertices, dtype=np.float64))
    out.export(dst)
    print(f'保存: {dst}', flush=True)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit('使いかた: apply_reference_detail.py 入力.glb 出力.glb --front=正面.png ...')
    main()
