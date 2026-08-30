# 色塗りの実体（ADR-0008）。★venv-21（Python 3.12）の中で動く。
#
# 直接呼ばずに tools/make_texture.py から呼ぶこと。make_texture は形づくりと同じ
# 環境（Python 3.11）で動き、このファイルを別プロセスとして起動する。
#
# ★上流をそのままでは動かせないので2つ補う。上流のファイルは書き換えない
#   1. basicsr が torchvision.transforms.functional_tensor を import するが、
#      torchvision 0.17 で消えている（この環境は 0.21）。橋渡しを作る。
#   2. テクスチャの穴埋め meshVerticeInpaint（C++拡張）が Windows 向けに
#      ビルドされておらず、上流にフォールバックも無い。Python で書いて差し込む。
#
# ★use_remesh=False は必須
#   既定の True は【渡した形を作り直してから塗る】。リトポロジー済みの形も、
#   頭と体に切り分けた形も置き換わってしまう。
#
# ★texture_size の【半分】が実際に出る絵の大きさ（実測 4096→2048、2048→1024）
#   下げても VRAM はほとんど変わらない（確保 20.4GB と 19.2GB）ので既定は 4096。
#
# ★塗り工程へ渡すメッシュは【Y上】でなければならない（2026-08-31 実測）
#   Z上のまま渡すと、球に近い頭で前後を取り違え、顔が頭の裏側に付く。
#   胴体は輪郭で前後が分かるので気づきにくいが、頭は壊れる。
#   --up=z（既定）なら、渡す前に Y上へ直し、返ってきたものを Z上へ戻す。
#
# ★出来上がるまで --out には何も置かない
#   途中で落ちたときに中途半端な glb が残ると、次の実行や下流の工程が
#   それを完成品として掴む。作業用の名前で作り、全部終わってから置き換える。
import argparse
import os
import sys
import time

# 塗りが作る中間ファイル（stem からの接尾辞）。★実行前に必ず消す
ARTIFACTS = ('.glb', '.obj', '.mtl', '.jpg',
             '_metallic.jpg', '_roughness.jpg', '_in.obj')
WORK_SUFFIX = '_work.glb'


def to_yup(v):
    """Z上 -> Y上。(x, y, z) -> (x, z, -y)"""
    import numpy as np
    v = np.asarray(v, dtype=np.float64)
    return np.stack([v[:, 0], v[:, 2], -v[:, 1]], 1)


def to_zup(v):
    """Y上 -> Z上。to_yup の逆。"""
    import numpy as np
    v = np.asarray(v, dtype=np.float64)
    return np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)


def use_utf8_stdout():
    """出力を UTF-8 にする。★import 時ではなく main の中で呼ぶ。

    トップレベルでやると、テストが import しただけで pytest の
    キャプチャ用ストリームを触り、reconfigure を持たない実装だと収集ごと落ちる。
    """
    fn = getattr(sys.stdout, 'reconfigure', None)
    if fn:
        fn(encoding='utf-8')


def fix_torchvision():
    """basicsr が見に行く古い置き場所へ橋渡しする。

    Hunyuan3D-2.1 同梱の torchvision_fix.py と同じ考えかた。
    """
    try:
        import torchvision.transforms.functional_tensor              # noqa: F401
        return False
    except ImportError:
        pass
    try:
        import types

        import torchvision.transforms.functional as F
    except ImportError as e:
        # ★torchvision そのものが無い。ここで死なずに先へ進めて、
        #   本来のエラー（上流の import 失敗）を見せる
        print(f'（basicsr 用の回避を適用できませんでした: {e}）', flush=True)
        return False
    mod = types.ModuleType('torchvision.transforms.functional_tensor')
    mod.rgb_to_grayscale = F.rgb_to_grayscale
    sys.modules['torchvision.transforms.functional_tensor'] = mod
    return True


def inpaint_fallback(texture, mask, *a, **kw):
    """テクスチャの塗り残しを、いちばん近い塗れている画素の色で埋める。

    上流の meshVerticeInpaint（C++拡張）の代わり。埋めるのは UV の島のすき間
    なので近傍の色で足りる。
    """
    import numpy as np
    tex = np.asarray(texture)
    m = np.asarray(mask)
    if m.ndim == 3:
        m = m[..., 0]
    filled = m > 0
    if filled.all() or not filled.any():
        return tex, (np.ones_like(m) if filled.any() else m)
    from scipy import ndimage
    _, (iy, ix) = ndimage.distance_transform_edt(
        ~filled, return_distances=True, return_indices=True)
    print(f'  穴埋め: {int((~filled).sum()):,} 画素', flush=True)
    return tex[iy, ix], np.ones_like(m)


def install_inpaint_fallback():
    """読み込み済みの MeshRender へ代替を差し込む。上流ファイルは触らない。

    ★上流を import した直後に呼ぶこと。MeshRender は textureGenPipeline の
      モジュール先頭で import されるので、その時点で sys.modules に入る。
      パイプラインを組み立てた後まで待つ理由は無く、待つほど上流の
      import タイミングの変化に弱くなる。
    """
    done = []
    for name, mod in list(sys.modules.items()):
        if name.endswith('MeshRender') and getattr(mod, 'meshVerticeInpaint', None) is None:
            setattr(mod, 'meshVerticeInpaint', inpaint_fallback)
            done.append(name)
    if done:
        print(f'穴埋めの代替を差し込みました: {", ".join(done)}', flush=True)
    else:
        # ★黙って通さない。上流がビルド済みなら不要だが、名前が変わった場合も
        #   ここに落ちる。最後の uv_inpaint で NameError になるまで気づけない
        print('※ 穴埋めの差し込み先が見つかりませんでした。'
              '上流に meshVerticeInpaint があるか、モジュール名が変わっています',
              flush=True)
    return done


def clear_stale(stem):
    """前回の中間ファイルを消す。

    ★これが無いと【2回目の実行で前回の形が出る】。
      上流は bpy を潰してあるぶん .glb を書かないので、glb は必ず
      こちら側で .obj から作る。前回の .glb が残っていると
      「作り直す必要が無い」と誤判定し、古い形へ新しい絵を貼ってしまう。
      しかもエラーは一切出ない。
    """
    removed = []
    for suffix in ARTIFACTS + (WORK_SUFFIX,):
        path = stem + suffix
        if os.path.isfile(path):
            os.remove(path)
            removed.append(os.path.basename(path))
    if removed:
        print(f'  前回の中間ファイルを消しました: {", ".join(removed)}', flush=True)
    return removed


def check_ckpt(root):
    """高精細化の重みを確認する。無ければ止まる。

    ★上流は Hunyuan3DPaintPipeline.__init__ の中でこれを読み込み、
      生の FileNotFoundError を投げる（実測）。モデルを載せる前に弾く。
    """
    ckpt = os.path.join(root, 'ckpt', 'RealESRGAN_x4plus.pth')
    if not os.path.isfile(ckpt):
        raise SystemExit(f'高精細化の重みがありません: {ckpt}\n'
                         'docs/setup/paint-environment.md の手順5を参照してください。')
    return ckpt


def import_upstream():
    """上流を読み込み、穴埋めの代替を差し込んで返す。

    ★差し込みはここでやる。呼び出し側に置くと「呼び忘れ」を検証できず、
      60秒走り切ったあとに NameError で落ちる形になる。
    """
    from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline
    install_inpaint_fallback()
    return Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline


def prepare_output(out):
    """出力先を整えて stem を返す。★前回の中間ファイルをここで必ず消す。"""
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    stem = os.path.splitext(out)[0]
    clear_stale(stem)
    return out, stem


def require_pbr_maps(stem):
    """金属・ざらつきのマップが揃っているか確認する。

    ★足りないときは黙って成功にしない。これを glb に入れるのが
      ADR-0008 の要点なので、飛ばした出力は目的を果たしていない。
    """
    met, rgh = f'{stem}_metallic.jpg', f'{stem}_roughness.jpg'
    missing = [p for p in (met, rgh) if not os.path.isfile(p)]
    if missing:
        raise SystemExit(
            '金属・ざらつきのマップが見つかりません: ' + ', '.join(missing) + '\n'
            'これを glb に入れるのが ADR-0008 の要点なので、成功にはしません。')
    return met, rgh


def configure(conf, args, repo, root):
    """上流の設定を埋める。★呼び出し側から検証できるよう関数に分けてある。"""
    conf.multiview_cfg_path = os.path.join(repo, 'hy3dpaint/cfgs/hunyuan-paint-pbr.yaml')
    conf.custom_pipeline = os.path.join(repo, 'hy3dpaint/hunyuanpaintpbr')
    conf.realesrgan_ckpt_path = os.path.join(root, 'ckpt', 'RealESRGAN_x4plus.pth')
    conf.texture_size = args.texsize
    conf.render_size = args.rendersize
    return conf


def run_paint(pipe, obj_in, front, out_obj):
    """上流を呼ぶ。★use_remesh=False は必須なので閉じ込めて検証できるようにする。"""
    return pipe(mesh_path=obj_in, image_path=os.path.abspath(front),
                output_mesh_path=out_obj,
                use_remesh=False,          # ★True だと渡した形が作り直される
                save_glb=True)


def metallic_roughness(m_im, r_im):
    """金属とざらつきを glTF の決まりで1枚にまとめる。

    ★G＝ざらつき、B＝金属。入れ替えると、つやのある所が金属になり
      金属の所がつや消しになる。見た目は「なんか変」でしか出ないので気づきにくい。
    """
    import numpy as np
    from PIL import Image
    if m_im.size != r_im.size:
        m_im = m_im.resize(r_im.size, Image.LANCZOS)
    w, h = r_im.size
    mr = np.zeros((h, w, 3), np.uint8)
    mr[..., 1] = np.asarray(r_im)          # G = ざらつき
    mr[..., 2] = np.asarray(m_im)          # B = 金属
    return mr


def attach_pbr(glb_path, stem):
    """金属・ざらつきのマップを glb に入れる。

    ★Factor はマップに掛かる係数なので、0 のままだとマップが打ち消される。
      金属だけは 0 にする（実測でこのマップは平均 6.8／最大 33（/255）で
      金属の画素が1つも無く、それでも金属が入っていると暗く沈むため）。
    ★足りないときは黙って成功にしない。この関数が在る理由そのものなので、
      飛ばした出力は目的を果たしていない。
    ★trimesh で読み書きし直すので、シーングラフとノード名は落ちる。
      パーツごとに塗ってから結合する順序なので今は困らない。
    """
    import numpy as np
    import trimesh
    from PIL import Image
    from trimesh.visual.material import PBRMaterial

    met, rgh = require_pbr_maps(stem)          # ★読み込む前に確認する
    mesh = trimesh.load(glb_path, force='mesh')
    mat = getattr(mesh.visual, 'material', None)
    if not isinstance(mat, PBRMaterial):
        mat = mat.to_pbr()
        mesh.visual.material = mat

    # 色の絵は、書き出された .jpg のほうが大きいことがあるので置き換える
    alb = f'{stem}.jpg'
    if os.path.exists(alb):
        im = Image.open(alb).convert('RGB')
        cur = getattr(mat, 'baseColorTexture', None)
        if cur is None or im.size[0] > cur.size[0]:
            mat.baseColorTexture = im
            print(f'  色の絵を {im.size[0]}px に差し替えました'
                  f'（glb の中は {cur.size[0] if cur else "なし"}px でした）', flush=True)

    m_im = Image.open(met).convert('L')
    r_im = Image.open(rgh).convert('L')
    mat.metallicRoughnessTexture = Image.fromarray(metallic_roughness(m_im, r_im))
    mat.metallicFactor = 0.0
    mat.roughnessFactor = 1.0
    mesh.export(glb_path)
    print(f'  金属・ざらつきのマップを入れました（{r_im.size[0]}px / '
          f'金属の平均 {np.asarray(m_im).mean():.0f}・'
          f'ざらつきの平均 {np.asarray(r_im).mean():.0f}）', flush=True)


def restore_up(glb_path):
    """Y上で塗った結果を Z上へ戻す（UVと材質は保つ）。"""
    import trimesh
    m = trimesh.load(glb_path, force='mesh', process=False)
    m.vertices = to_zup(m.vertices)
    m.export(glb_path)
    print('  Z上へ戻しました', flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='形に色を塗る（venv-21 の中で動く）')
    p.add_argument('mesh', help='塗る対象の形の glb')
    p.add_argument('front', help='正面の絵')
    p.add_argument('out', help='出力する glb')
    p.add_argument('--paint-root', required=True,
                   help='Hunyuan3D-2.1 と ckpt がある場所')
    p.add_argument('--up', choices=['y', 'z'], default='z',
                   help='渡すメッシュの上方向。★上流は Y上 を前提にしている。'
                        'z なら渡す前に直し、返ってきたものを戻す')
    p.add_argument('--texsize', type=int, default=4096)
    p.add_argument('--rendersize', type=int, default=1024)
    p.add_argument('--views', type=int, default=6)
    p.add_argument('--res', type=int, default=512)
    return p.parse_args(argv)


def setup_paths(root):
    """上流を import できるようにして、リポジトリの場所を返す。"""
    repo = os.path.join(root, 'Hunyuan3D-2.1')
    if not os.path.isdir(repo):
        raise SystemExit(f'Hunyuan3D-2.1 が見つかりません: {repo}')
    for p in (repo, os.path.join(repo, 'hy3dpaint')):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    return repo


def main(argv=None):
    use_utf8_stdout()
    args = parse_args(argv)
    root = os.path.abspath(args.paint_root)
    check_ckpt(root)                           # ★モデルを載せる前に弾く
    if fix_torchvision():
        print('basicsr 用の回避を適用しました', flush=True)
    repo = setup_paths(root)

    import torch
    import trimesh
    Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline = import_upstream()

    dev = torch.cuda.get_device_properties(0)
    print(f'GPU: {dev.name} / {dev.total_memory / 1024 ** 3:.2f}GB / '
          f'sm_{dev.major}{dev.minor}', flush=True)

    out, stem = prepare_output(args.out)
    m = trimesh.load(args.mesh, force='mesh')
    verts = m.vertices
    if args.up == 'z':
        # ★上流は Y上 を前提にしている。Z上のまま渡すと頭の前後を取り違える
        print('  Y上に直して渡します（返ってきたら Z上へ戻します）', flush=True)
        verts = to_yup(verts)
    obj_in = stem + '_in.obj'
    trimesh.Trimesh(vertices=verts, faces=m.faces).export(obj_in)

    conf = configure(Hunyuan3DPaintConfig(args.views, args.res), args, repo, root)
    print(f'色塗り(2.1): 面 {len(m.faces):,} / テクスチャの器 {conf.texture_size}'
          f'（実際に出る絵は {conf.texture_size // 2}） / 描画 {conf.render_size} / '
          f'工程の絵 {conf.resolution}', flush=True)

    torch.cuda.reset_peak_memory_stats()
    t = time.time()
    pipe = Hunyuan3DPaintPipeline(conf)
    out_obj = run_paint(pipe, obj_in, args.front, stem + '.obj')
    print(f'色塗り(2.1) 完了 {time.time() - t:.1f}秒 / GPUメモリ最大 '
          f'使用 {torch.cuda.max_memory_allocated() / 1024 ** 3:.2f}GB・'
          f'確保 {torch.cuda.max_memory_reserved() / 1024 ** 3:.2f}GB', flush=True)

    # ★作業用の名前で組み立て、全部終わってから --out へ置く。
    #   途中で落ちたときに中途半端な glb を掴ませないため
    # ★上流の glb 変換は【必ず静かに失敗する】（2026-08-31 に確定）。
    #   convert_obj_to_glb は bpy を使い、失敗すると try/except Exception で
    #   False を返すだけ（mesh_utils.py:274-290）。venv-21 に bpy は入らない
    #   （Python 3.11 用しか無い）ので、この分岐は現状いつも else を通る。
    #   bpy を入れた環境なら上流側のほうが良い変換になるので、分岐は残す。
    work = stem + WORK_SUFFIX
    made = stem + '.glb'
    if os.path.isfile(made):
        print('  上流が glb を書きました（bpy が入っている環境）', flush=True)
        os.replace(made, work)
    else:
        got = out_obj if isinstance(out_obj, str) and os.path.isfile(out_obj) else None
        if got is None:
            raise SystemExit('色塗り(2.1) の出力が見つかりません')
        trimesh.load(got, force='mesh').export(work)
    attach_pbr(work, stem)
    if args.up == 'z':
        restore_up(work)                       # ★渡されたときの向きに戻す
    os.replace(work, out)
    print(f'保存: {args.out}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
