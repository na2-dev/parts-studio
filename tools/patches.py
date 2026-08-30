# 上流（TRELLIS.2）の非互換を埋める。ファイルは書き換えない。
#
# ★なぜ要るか
#   上流は特定バージョンの transformers と、gated な重みを前提にしている。
#   こちらの環境では2箇所で動かないので、import 後に差し替える。
#   上流を書き換えると更新のたびに当て直しになるため、こちらで吸収する。
#
#   どちらも場当たりの回避ではなく、実測で裏を取った判断:
#     ../docs/measurements/2026-08-30-trellis2-first-real-run.md（踏んだ経緯と数値）
#     ../docs/setup/trellis2-windows.md（ハマりどころ）
import sys


def _fix_dinov3():
    """transformers 5.x で DINOv3 の層が移動した件を埋める。

    ★上流は self.model.layer を直接舐めるが、transformers 5.16 では
      層が model.model.layer へ移り AttributeError になる。
      内部属性に触らず、公開APIの output_hidden_states=True で
      同じもの（最終層の出力・norm 前）を取る。
      hidden_states[-1] が norm 前であることは実測で確認済み
      （last_hidden_state との差の最大が 153459 と大きく、別物）。
      → ../docs/measurements/2026-08-30-trellis2-first-real-run.md
    """
    import torch.nn.functional as F
    from trellis2.modules import image_feature_extractor as ife

    class DinoV3Fixed(ife.DinoV3FeatureExtractor):
        def extract_features(self, image):
            image = image.to(next(self.model.parameters()).dtype)
            out = self.model(pixel_values=image, output_hidden_states=True)
            h = out.hidden_states[-1]
            return F.layer_norm(h, h.shape[-1:])

    ife.DinoV3FeatureExtractor = DinoV3Fixed
    return 'DINOv3（transformers 5.x で層が移動した件）'


def _skip_rembg():
    """背景ぬき（RMBG-2.0）を読まないようにする。

    ★入力は切り抜き済みの透過PNGなので、上流の preprocess_image は
      背景ぬきを呼ばない（アルファが全て 255 でなければ素通し）。
      ところが from_pretrained の時点で【モデルを構築してしまう】ため、
      gated な briaai/RMBG-2.0 を取りに行って 401 で落ちる。
      構築だけ避ける。呼ばれたら明示的に落として、前提の破れに気づけるようにする。
      → ../docs/setup/trellis2-windows.md の「RMBG-2.0 を不要にする」
    """
    from trellis2.pipelines import rembg

    class SkipRembg:
        def __init__(self, model_name):
            self.model_name = model_name

        def to(self, device):
            return self

        def cpu(self):
            return self

        def __call__(self, image):
            raise RuntimeError(
                '背景ぬきが呼ばれました。入力は切り抜き済みの透過PNGにしてください'
                '（アルファが全て不透明だと上流が背景ぬきを呼びます）')

    rembg.BiRefNet = SkipRembg
    return '背景ぬき（RMBG-2.0 を読まない。入力は透過PNG前提）'


def apply(verbose=True):
    """すべて当てる。TRELLIS.2 を import できる状態で呼ぶこと。"""
    applied = [_fix_dinov3(), _skip_rembg()]
    if verbose:
        for a in applied:
            print(f'  上流を補いました: {a}', file=sys.stderr, flush=True)
    return applied
