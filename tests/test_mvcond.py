# tools/mvcond.py のテスト。GPU は要らない（CPU テンソルで足りる）。
#
# 実行: venv\Scripts\python.exe -m pytest tests\test_mvcond.py -q
import os
import sys

import pytest
import torch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import mvcond                                                    # noqa: E402


# ★上流の呼び出しは _get_model_prediction → self._inference_model(
#   model, x_t, t, cond, **kwargs) で、kwargs に neg_cond /
#   guidance_strength / guidance_interval が入る（flow_euler.py:48-51、
#   guidance_interval_mixin.py:9）。既定値を持たせないので、
#   inject が **kw を素通ししなくなると TypeError で落ちる。
REQUIRED_KW = {'neg_cond', 'guidance_strength', 'guidance_interval'}


class FakeSampler:
    """_inference_model を持つだけの器。呼ばれた cond と kwargs を記録する。"""

    def __init__(self):
        self.seen = []
        self.seen_kw = []

        def base(model, x_t, t, cond, neg_cond, guidance_strength,
                 guidance_interval, **kw):
            self.seen.append(cond.clone())
            self.seen_kw.append({'neg_cond': neg_cond,
                                 'guidance_strength': guidance_strength,
                                 'guidance_interval': guidance_interval, **kw})
            return cond.sum(dim=(1, 2))          # (B,) を返す。平均が検算しやすい形

        self._inference_model = base
        self._base = base


def call(sampler, cond, **extra):
    """実物と同じ形でサンプラーを呼ぶ。"""
    return sampler._inference_model(
        None, None, 0.5, cond['cond'],
        neg_cond=cond['neg_cond'], guidance_strength=7.5,
        guidance_interval=(0.0, 1.0), **extra)


def make_cond(v=4, n=5, d=3):
    """View ごとに値が違う (V, N, D) を作る。View i の中身は全部 i。

    ★neg_cond はゼロにしない。ゼロだと「concat がゼロで埋め直したか」を
      検証できず、prepare_cond が neg_cond を触らなくてもテストが通ってしまう。
    """
    c = torch.stack([torch.full((n, d), float(i)) for i in range(v)])
    return {'cond': c, 'neg_cond': torch.full_like(c, -9.0)}


# ---- concat_views ---------------------------------------------------------

def test_concat_views_は形をV倍のトークンにする():
    c = make_cond(v=4, n=5, d=3)['cond']
    out = mvcond.concat_views(c)
    assert out.shape == (1, 20, 3)


def test_concat_views_はViewの順序を保つ():
    c = make_cond(v=3, n=2, d=1)['cond']
    out = mvcond.concat_views(c)[0, :, 0]
    assert out.tolist() == [0, 0, 1, 1, 2, 2]


def test_concat_views_は3次元以外を拒む():
    with pytest.raises(ValueError):
        mvcond.concat_views(torch.zeros(4, 5))


# ---- prepare_cond ---------------------------------------------------------

def test_prepare_cond_concat_はneg_condも連結後の形にする():
    cond = mvcond.prepare_cond(make_cond(v=4, n=5, d=3), 'concat')
    assert cond['cond'].shape == (1, 20, 3)
    assert cond['neg_cond'].shape == (1, 20, 3)
    # ★make_cond の neg_cond は -9 なので、ゼロなら埋め直された証拠
    assert torch.count_nonzero(cond['neg_cond']) == 0


def test_prepare_cond_single_は正面だけ残す():
    cond = mvcond.prepare_cond(make_cond(v=4, n=5, d=3), 'single')
    assert cond['cond'].shape == (1, 5, 3)
    assert cond['cond'][0, 0, 0] == 0          # View 0 が正面


@pytest.mark.parametrize('mode', ['multidiffusion', 'stochastic'])
def test_prepare_cond_はneg_condを1つに減らす(mode):
    cond = mvcond.prepare_cond(make_cond(v=4, n=5, d=3), mode)
    assert cond['cond'].shape == (4, 5, 3)     # cond は 4 View のまま
    assert cond['neg_cond'].shape == (1, 5, 3)


def test_prepare_cond_は知らない方式を拒む():
    with pytest.raises(ValueError):
        mvcond.prepare_cond(make_cond(), 'unknown')


# ---- inject ---------------------------------------------------------------

def test_inject_stochastic_はステップごとにViewを切り替える():
    s = FakeSampler()
    cond = mvcond.prepare_cond(make_cond(v=4, n=2, d=1), 'stochastic')
    with mvcond.inject(s, 4, 'stochastic'):
        for _ in range(6):
            call(s, cond)
    used = [c[0, 0, 0].item() for c in s.seen]
    assert used == [0, 1, 2, 3, 0, 1]


def test_inject_stochastic_はcascade相当の回数でも尽きない():
    # ★1024_cascade は同じサンプラーを 512 と 1024 で 2 回使う（12 + 12 ステップ）。
    #   有限列だと途中で StopIteration になる
    s = FakeSampler()
    cond = mvcond.prepare_cond(make_cond(v=4, n=2, d=1), 'stochastic')
    with mvcond.inject(s, 4, 'stochastic'):
        for _ in range(24):
            call(s, cond)
    used = [c[0, 0, 0].item() for c in s.seen]
    assert used == [0, 1, 2, 3] * 6


def test_inject_multidiffusion_は全Viewの平均を返す():
    s = FakeSampler()
    cond = mvcond.prepare_cond(make_cond(v=4, n=2, d=1), 'multidiffusion')
    with mvcond.inject(s, 4, 'multidiffusion'):
        out = call(s, cond)
    # 各 View は sum = 値 x 2要素。平均は (0+2+4+6)/4 = 3
    assert out.item() == pytest.approx(3.0)
    assert len(s.seen) == 4                     # 4 View ぶん呼ばれる


@pytest.mark.parametrize('mode', ['multidiffusion', 'stochastic'])
def test_inject_はkwargsをそのまま渡す(mode):
    # ★ここが落ちると実機の1ステップ目で TypeError になる。
    #   guidance_strength / guidance_interval は既定値なしで受け取られる
    s = FakeSampler()
    cond = mvcond.prepare_cond(make_cond(v=4, n=2, d=1), mode)
    with mvcond.inject(s, 4, mode):
        call(s, cond, concat_cond=None)
    assert s.seen_kw, 'サンプラーが呼ばれていない'
    for kw in s.seen_kw:
        assert REQUIRED_KW <= set(kw)
        assert kw['guidance_strength'] == 7.5
        assert kw['guidance_interval'] == (0.0, 1.0)
        assert 'concat_cond' in kw              # 知らない kwargs も落とさない


@pytest.mark.parametrize('mode', ['multidiffusion', 'stochastic'])
def test_inject_はneg_condをViewごとに切らない(mode):
    # ★prepare_cond が neg_cond を 1 つに減らしている。ここで更に切ると
    #   batch が 0 になって CFG が壊れる
    s = FakeSampler()
    cond = mvcond.prepare_cond(make_cond(v=4, n=2, d=1), mode)
    expect = cond['neg_cond'].clone()
    with mvcond.inject(s, 4, mode):
        for _ in range(4):                      # ★1回だけだと View 0 しか通らない。
            call(s, cond)                       #   neg_cond[0:1] は無変化なので見逃す
    assert len(s.seen_kw) >= 4
    for kw in s.seen_kw:
        assert kw['neg_cond'].shape == (1, 2, 1)
        assert torch.equal(kw['neg_cond'], expect)
    assert torch.equal(cond['neg_cond'], expect)   # 呼び出しが cond を汚さない


@pytest.mark.parametrize('mode', ['concat', 'single'])
def test_inject_は包む必要のない方式では素通しする(mode):
    s = FakeSampler()
    before = s._inference_model
    with mvcond.inject(s, 4, mode):
        assert s._inference_model is before
    assert s._inference_model is before


def test_inject_はView1つなら包まない():
    s = FakeSampler()
    before = s._inference_model
    with mvcond.inject(s, 1, 'multidiffusion'):
        assert s._inference_model is before


def test_inject_は抜けたあと元に戻す():
    s = FakeSampler()
    before = s._inference_model
    with mvcond.inject(s, 4, 'multidiffusion'):
        assert s._inference_model is not before
    assert s._inference_model is before


def test_inject_はクラスのメソッドをインスタンス属性で隠したままにしない():
    # ★代入で戻すと sampler.__dict__ に束縛メソッドが残り、循環参照になる
    class ClassLevel:
        def __init__(self):
            self.seen = []

        def _inference_model(self, model, x_t, t, cond, **kw):
            self.seen.append(cond.clone())
            return cond.sum(dim=(1, 2))

    s = ClassLevel()
    assert '_inference_model' not in vars(s)
    with mvcond.inject(s, 4, 'multidiffusion'):
        assert '_inference_model' in vars(s)
    assert '_inference_model' not in vars(s)


def test_inject_は例外が出ても元に戻す():
    # ★戻し忘れると、次のサンプラー呼び出しが壊れたまま走る
    s = FakeSampler()
    before = s._inference_model
    with pytest.raises(RuntimeError):
        with mvcond.inject(s, 4, 'multidiffusion'):
            raise RuntimeError('途中で落ちた')
    assert s._inference_model is before


def test_inject_は知らない方式を拒む():
    with pytest.raises(ValueError):
        with mvcond.inject(FakeSampler(), 4, 'unknown'):
            pass
