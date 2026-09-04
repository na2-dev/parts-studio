# 上流に当てるパッチ

塗り環境を自前で作るときに使う（[手順](../docs/setup/paint-environment.md)）。

| パッチ | 対象 | 何を直すか |
| :--- | :--- | :--- |
| `hunyuan3d-2.1_paint.patch` | `Hunyuan3D-2.1` | `hy3dpaint/DifferentiableRenderer/mesh_utils.py` の `import bpy` を try/except にする |

## なぜ bpy を外すのか

**bpy は Python 3.11 用しか無く、塗り環境（3.12）には入らない。**
パッチが無いと `import bpy` の `ImportError` でモジュールごと読めない。
**このパッチが避けているのはそれだけ**である。

### その結果、上流は glb を書かない（2026-08-31 に確定）

上流が bpy を使っているのは `convert_obj_to_glb` だけだが、その中身は
`try: ... except Exception: return False`（`mesh_utils.py:274-290`）。
`bpy = None` にすると `bpy.ops.wm.obj_import` が `AttributeError` になり、
**呼び出し側には何も伝わらないまま False が返る**（`textureGenPipeline.py:188-190` は
戻り値を見ていない）。venv-21 に `bpy` が入っていないことも実測で確認済み。

したがって `save_glb=True` を渡しても `.glb` は**一度もできない**。
glb は必ず `paint_backend.py` 側が `.obj` から作っている。

**ここに落とし穴がある。** 前回の `.glb` が残っていると「上流が書いた」と
誤判定して古い形を使ってしまうので、`paint_backend.clear_stale()` が
実行前に中間ファイルを消している。

## 当てかた

`paint/` の中（`Hunyuan3D-2.1/` の親）で実行する。

```powershell
cd C:\work\parts-studio\paint
git -C Hunyuan3D-2.1 apply ..\..\patches\hunyuan3d-2.1_paint.patch
```

`--depth 1` の clone の HEAD に対して当てる前提。上流が該当箇所を書き換えると
当たらなくなる。失敗したら `import bpy` を手で次の 4 行に置き換える。

```python
try:
    import bpy
except ImportError:
    bpy = None
```

## ここに入れないもの

- **上流の実行時の非互換への手当て**は、パッチではなく `tools/paint_backend.py` が
  `sys.modules` へ差し込む形で行っている（basicsr の torchvision 問題と、
  `meshVerticeInpaint` の C++ 拡張が無い問題）。上流のファイルを書き換えないため。
- **形づくり側（`hy3dshape`）のパッチ**は持たない。parts-studio の形づくりは
  TRELLIS.2（[ADR-0003](../docs/adr/0003-trellis2-for-shape-multiview-deferred.md)）で、
  Hunyuan3D の形づくりは使わない。
