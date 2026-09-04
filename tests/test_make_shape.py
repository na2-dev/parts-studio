# tools/make_shape.py の引数まわりのテスト。GPU も torch も要らない。
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import make_shape                                              # noqa: E402


@pytest.fixture
def imgs(tmp_path):
    made = {}
    for v in ('front', 'left', 'right', 'back'):
        p = tmp_path / f'{v}.png'
        p.write_bytes(b'\x89PNG\r\n\x1a\n')      # 中身は見ないので頭だけ
        made[v] = str(p)
    return made


def test_正面だけでも通る(imgs):
    a = make_shape.parse_args(['--front', imgs['front'], '--out', 'o.glb'])
    assert make_shape.collect_images(a) == {'front': imgs['front']}


def test_4枚渡すと4枚集まる(imgs):
    a = make_shape.parse_args([f'--{v}={p}' for v, p in imgs.items()] + ['--out', 'o.glb'])
    assert set(make_shape.collect_images(a)) == set(imgs)


def test_Viewの並びは正面が先頭(imgs):
    # ★mvcond は View 0 を正面とみなす（--mode single はそれだけを使う）。
    #   並べ替えると single が左向きの絵で形を作り、stochastic の割り当てもずれる
    assert make_shape.VIEWS[0] == 'front'
    assert make_shape.VIEWS == ('front', 'left', 'right', 'back')


def test_正面が無いとエラー(imgs):
    with pytest.raises(SystemExit):
        make_shape.parse_args(['--left', imgs['left'], '--out', 'o.glb'])


def test_出力先が無いとエラー(imgs):
    with pytest.raises(SystemExit):
        make_shape.parse_args(['--front', imgs['front']])


def test_空文字の正面はargparseを素通りするのでcollectで止める(imgs):
    # ★required=True は "" を通してしまう。collect_images 側の分岐が効いている
    a = make_shape.parse_args(['--front', '', '--out', 'o.glb'])
    with pytest.raises(SystemExit) as e:
        make_shape.collect_images(a)
    assert '正面' in str(e.value)


def test_絵が存在しないと止まる(tmp_path, imgs):
    a = make_shape.parse_args(['--front', str(tmp_path / 'ない.png'), '--out', 'o.glb'])
    with pytest.raises(SystemExit) as e:
        make_shape.collect_images(a)
    assert '見つかりません' in str(e.value)


def test_既定は実際に通しで検証した方式(imgs):
    # ★ADR-0005 は concat を既定としたが、通しで検証したのは multidiffusion。
    #   根拠が変わった経緯は ADR-0005 冒頭の「訂正あり（2026-08-31）」を参照
    a = make_shape.parse_args(['--front', imgs['front'], '--out', 'o.glb'])
    assert a.mode == 'multidiffusion'
    assert a.res == 1024
    assert a.seed == 1234


def test_方式の一覧はmvcondを正とする(imgs):
    # ★二重定義すると --help の並びと ADR の記述がずれる
    import mvcond
    a = make_shape.parse_args(['--front', imgs['front'], '--out', 'o.glb'])
    assert a.mode in mvcond.MODES
    for m in mvcond.MODES:
        assert make_shape.parse_args(
            ['--front', imgs['front'], '--out', 'o.glb', '--mode', m]).mode == m


def test_知らない方式は拒む(imgs):
    with pytest.raises(SystemExit):
        make_shape.parse_args(['--front', imgs['front'], '--out', 'o.glb', '--mode', 'unknown'])


# ---- 解像度とトークン上限 -------------------------------------------------
# ★上流の打ち切りは `num_tokens < max_num_tokens or hr_resolution == 1024` で、
#   下げ幅は 128 固定（trellis2_image_to_3d.py:335-339）。1024 に着地しない値は
#   1024 を跨いで下がり続ける。

@pytest.mark.parametrize('res', [1024, 1152, 1280, 1408, 1536, 2048])
def test_1024に着地する解像度は通る(imgs, res):
    a = make_shape.parse_args(['--front', imgs['front'], '--out', 'o.glb',
                               '--res', str(res)])
    assert a.res == res
    assert (a.res - 1024) % 128 == 0        # 上流のラダーが 1024 で止まれる


@pytest.mark.parametrize('res', [256, 512, 896, 1000, 1100, 1537])
def test_1024に着地しない解像度は拒む(imgs, res):
    # ★1000 や 1100 を通すと、トークン上限を小さくしたときに戻ってこなくなる
    with pytest.raises(SystemExit):
        make_shape.parse_args(['--front', imgs['front'], '--out', 'o.glb',
                               '--res', str(res)])


@pytest.mark.parametrize('n', ['0', '-1'])
def test_トークン上限が0以下なら拒む(imgs, n):
    # ★0 だと上流の `num_tokens < max_num_tokens` が永久に偽になる
    with pytest.raises(SystemExit):
        make_shape.parse_args(['--front', imgs['front'], '--out', 'o.glb',
                               '--max-tokens', n])


# ---- 出力先 ---------------------------------------------------------------

def test_出力先に拡張子が無いと生成の前に拒む(imgs):
    # ★ここで弾かないと、3分かけて生成したあとに trimesh が落ちて成果物が消える
    with pytest.raises(SystemExit):
        make_shape.parse_args(['--front', imgs['front'], '--out', 'out/shape'])


def test_TRELLIS2が無い場所を指すと止まる(tmp_path):
    with pytest.raises(SystemExit) as e:
        make_shape.resolve_repo(str(tmp_path / 'ない'))
    assert 'trellis2-windows.md' in str(e.value).lower() or '見つかりません' in str(e.value)


def test_repo未指定ならリポジトリ直下のTRELLIS2を見る():
    # 実在しない環境でも、期待するパスを組み立てられることだけ確かめる
    import make_shape as m
    expect = os.path.join(ROOT, 'TRELLIS.2')
    try:
        got = m.resolve_repo(None)
    except SystemExit as e:
        assert expect in str(e)
        return
    assert got == expect
