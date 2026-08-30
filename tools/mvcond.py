# 4枚の絵を条件として渡すための細工（ADR-0005）。
#
# ★なぜ要るか
#   TRELLIS.2 は絵を1枚しか受け取らない（run() の引数が Image 1枚）。
#   4枚を渡すには、サンプラーが毎ステップ呼ぶ推論関数を包んで、
#   条件の配り方を変えるしかない。
#
# ★包む位置
#   サンプラーの【最外層】（GuidanceIntervalSamplerMixin._inference_model）。
#   CFG が cond と neg_cond を分ける前に View を選ぶ必要があるため。
#   neg_cond は batch 1 のまま kwargs を素通りする。
#
# ★方式（[ADR-0005](../docs/adr/0005-multiview-conditioning-by-token-concat.md)）
#   concat          … 4枚分の特徴をトークン方向に連結し (1, V*N, D) で一度に渡す
#   multidiffusion  … 毎ステップ全 View で予測して平均。View 数倍のコスト
#   stochastic      … ステップごとに使う View を順に切り替える。追加コストほぼ0
#   single          … 正面1枚だけ（比較用）
import contextlib
import itertools

MODES = ('concat', 'multidiffusion', 'stochastic', 'single')


def concat_views(cond_tensor):
    """(V, N, D) を (1, V*N, D) にする。

    cross-attention なのでトークン数は可変。View がいくつでも受けられる。
    """
    if cond_tensor.ndim != 3:
        raise ValueError(f'(V, N, D) を渡すこと。受け取った形: {tuple(cond_tensor.shape)}')
    v, n, d = cond_tensor.shape
    return cond_tensor.reshape(1, v * n, d)


def prepare_cond(cond, mode):
    """cond 辞書（'cond' と 'neg_cond'）を方式に合わせて整える。

    ★破壊的に書き換える。呼び出し側は使い回さないこと。
    """
    if mode not in MODES:
        raise ValueError(f'方式は {MODES} のいずれか。受け取った値: {mode!r}')
    if mode == 'concat':
        import torch
        cond['cond'] = concat_views(cond['cond'])
        cond['neg_cond'] = torch.zeros_like(cond['cond'])
    elif mode == 'single':
        cond['cond'] = cond['cond'][:1]
        cond['neg_cond'] = cond['neg_cond'][:1]
    else:
        # multidiffusion / stochastic は View ごとに切り出して使う。
        # neg は 1 つで足りる（View に依らないため）。
        cond['neg_cond'] = cond['neg_cond'][:1]
    return cond


def view_order(num_views, steps):
    """stochastic で各ステップに使う View の番号を返す。

    ★ステップ数に依らず尽きないよう cycle を使う。
      1024_cascade は shape サンプラーを 2 回（512 と 1024）使うため、
      ステップ数ぶんの配列を先に作る方式だと足りなくなる。
    """
    it = itertools.cycle(range(num_views))
    return [next(it) for _ in range(steps)]


@contextlib.contextmanager
def inject(sampler, num_views, mode):
    """サンプラーの推論関数を包んで、多視点の条件を配る。

    concat / single は包む必要が無い（条件側で処理済み）ので素通しする。
    """
    if mode not in MODES:
        raise ValueError(f'方式は {MODES} のいずれか。受け取った値: {mode!r}')
    if mode in ('concat', 'single') or num_views <= 1:
        yield
        return

    old = sampler._inference_model
    if mode == 'stochastic':
        idx = itertools.cycle(range(num_views))

        def new(model, x_t, t, cond, **kw):
            i = next(idx)
            return old(model, x_t, t, cond=cond[i:i + 1], **kw)
    else:                                    # multidiffusion
        def new(model, x_t, t, cond, **kw):
            preds = [old(model, x_t, t, cond=cond[i:i + 1], **kw)
                     for i in range(num_views)]
            return sum(preds) / num_views

    sampler._inference_model = new
    try:
        yield
    finally:
        sampler._inference_model = old
