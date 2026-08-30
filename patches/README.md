# 上流に当てるパッチ

塗り環境を自前で作るときに使う（[手順](../docs/setup/paint-environment.md)）。

| パッチ | 対象 | 何を直すか |
| :--- | :--- | :--- |
| `hunyuan3d-2.1_paint.patch` | `Hunyuan3D-2.1` | `hy3dpaint/DifferentiableRenderer/mesh_utils.py` の `import bpy` を try/except にする |

## なぜ bpy を外すのか

**bpy は Python 3.11 用しか無く、塗り環境（3.12）には入らない。**
上流がこれを使っているのは最後の OBJ→GLB 変換だけで、
`paint_backend.py` は `save_glb=True` の経路を使うので通らない。

## 当てかた

```powershell
git -C Hunyuan3D-2.1 apply ..\..\patches\hunyuan3d-2.1_paint.patch
```

`--depth 1` の clone の HEAD に対して当てる前提。上流が該当箇所を書き換えると
当たらなくなるので、失敗したら手で同じ変更を入れる（3行の try/except）。

## ここに入れないもの

- **上流の実行時の非互換への手当て**は、パッチではなく `tools/paint_backend.py` が
  `sys.modules` へ差し込む形で行っている（basicsr の torchvision 問題と、
  `meshVerticeInpaint` の C++ 拡張が無い問題）。上流のファイルを書き換えないため。
- **形づくり側（`hy3dshape`）のパッチ**は持たない。parts-studio の形づくりは
  TRELLIS.2（[ADR-0003](../docs/adr/0003-trellis2-for-shape-multiview-deferred.md)）で、
  Hunyuan3D の形づくりは使わない。
