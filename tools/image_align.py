# 入力画像を「向きどうしで大きさ・高さがそろった状態」に整える。
#
# ★なぜ必要か（2026-08-16 実測）
#   Hunyuan は渡された絵を1枚ずつ recenter（中身を枠いっぱいに拡大）します。
#   このとき使う倍率は【その絵の中身の縦横の大きい方】で決まるので、
#   正面・横・後ろで倍率がバラバラになります。実測では
#       正面 0.892 ／ 横 0.857 ／ 後ろ 0.889   （約4%のズレ）
#   AIから見ると「正面と横で背の高さが違うキャラ」に見えるので、
#   形があいまいになります。
#
#   ここで先に【背の高さ】を基準にそろえておけば、そのあとの recenter は
#   どの向きでも同じ倍率になり、ズレが消えます。
#   （立ちキャラは どの向きから見ても背の高さは同じ。横幅は向きで変わるので使えません）
import numpy as np
from PIL import Image


def _bbox(img):
    """中身（透明でない部分）の囲い枠を返す。無ければ None"""
    a = np.asarray(img.convert('RGBA'))
    m = a[..., 3] > 8
    if not m.any():
        return None
    ys, xs = np.nonzero(m)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def align_views(images, border=0.10, verbose=True):
    """{'front': PIL, 'left': PIL, ...} を、背の高さがそろった正方形の絵にして返す。

    border は上下左右に空ける余白の割合。
    """
    boxes = {}
    for k, im in images.items():
        b = _bbox(im)
        if b is None:
            if verbose:
                print(f'そろえ: {k} は中身が見つからないので、そのまま使います', flush=True)
            return images          # 1枚でも判定できなければ、何もしない（安全側）
        boxes[k] = b

    heights = {k: b[3] - b[1] for k, b in boxes.items()}
    widths = {k: b[2] - b[0] for k, b in boxes.items()}

    # 横のほうが縦より大きい絵が混ざっていると、この方法では そろえられない
    # （recenter が横基準になってしまうため）。そのときは触らない。
    if any(widths[k] > heights[k] for k in boxes):
        if verbose:
            print('そろえ: 横長の絵があるため、そろえ処理は行いません', flush=True)
        return images

    target_h = max(heights.values())              # いちばん大きい絵に合わせる（画質を落とさない）
    side = int(round(target_h / (1.0 - 2 * border)))

    out = {}
    for k, im in images.items():
        im = im.convert('RGBA')
        x0, y0, x1, y1 = boxes[k]
        s = target_h / heights[k]                 # ★どの向きも「背の高さ」で同じ倍率にする
        nw, nh = max(1, int(round(widths[k] * s))), max(1, int(round(heights[k] * s)))
        crop = im.crop((x0, y0, x1, y1)).resize((nw, nh), Image.LANCZOS)
        canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
        canvas.paste(crop, ((side - nw) // 2, (side - nh) // 2), crop)
        out[k] = canvas
        if verbose:
            print(f'そろえ: {k} 中身 {widths[k]}x{heights[k]} → 倍率 {s:.3f} '
                  f'／ {side}x{side} の枠に配置', flush=True)
    if verbose:
        print(f'そろえ: これで どの向きも同じ倍率になります（背の高さ {target_h}px 基準）',
              flush=True)
    return out
