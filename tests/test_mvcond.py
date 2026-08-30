# tools/mvcond.py のテスト。GPU は要らない（CPU テンソルで足りる）。
#
# 実行: venv\Scripts\python.exe -m pytest tests\test_mvcond.py -q
import os
import sys

import pytest
import torch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import mvcond                                                    # noqa: E402


class FakeSampler:
    """_inference_model を持つだけの器。呼ばれた cond を記録する。"""

    def __init__(self):
        self.seen = []

        def base(model, x_t, t, cond, **kw):
            self.seen.append(cond.clone())
            return cond.sum(dim=(1, 2))          # (B,) を返す。平均が検算しやすい形

        self._inference_model = base
        self._base = base


def make_cond(v=4, n=5, d=3):
    """View ごとに値が違う (V, N, D) を作る。View i の中身は全部 i。"""
    c = torch.stack([torch.full((n, d), float(i)) for i in range(v)])
    return {'cond': c, 'neg_cond': torch.zeros_like(c)}


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


# ---- view_order -----------------------------------------------------------

def test_view_order_はステップ数がView数を超えても尽きない():
    # ★1024_cascade は shape サンプラーを2回使うので、ここが尽きると落ちる
    assert mvcond.view_order(4, 12) == [0, 1, 2, 3] * 3


def test_view_order_はView数の方が多くても足りる():
    assert mvcond.view_order(4, 2) == [0, 1]


# ---- inject ---------------------------------------------------------------

def test_inject_stochastic_はステップごとにViewを切り替える():
    s = FakeSampler()
    cond = mvcond.prepare_cond(make_cond(v=4, n=2, d=1), 'stochastic')
    with mvcond.inject(s, 4, 'stochastic'):
        for _ in range(6):
            s._inference_model(None, None, 0.5, cond['cond'])
    used = [c[0, 0, 0].item() for c in s.seen]
    assert used == [0, 1, 2, 3, 0, 1]


def test_inject_multidiffusion_は全Viewの平均を返す():
    s = FakeSampler()
    cond = mvcond.prepare_cond(make_cond(v=4, n=2, d=1), 'multidiffusion')
    with mvcond.inject(s, 4, 'multidiffusion'):
        out = s._inference_model(None, None, 0.5, cond['cond'])
    # 各 View は sum = 値 x 2要素。平均は (0+2+4+6)/4 = 3
    assert out.item() == pytest.approx(3.0)
    assert len(s.seen) == 4                     # 4 View ぶん呼ばれる


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
