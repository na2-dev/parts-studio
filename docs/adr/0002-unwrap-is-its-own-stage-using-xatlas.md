# UV展開を独立した工程にし、xatlas で自前に行う

> **訂正あり（2026-08-30）**: xatlas を既定にする部分は実測で覆った。展開器より前に
> リトポロジーが要る。[ADR-0007](0007-retopology-before-uv.md) を参照。
>
> **さらに訂正（2026-08-31）**: この ADR が「却下」とした
> **「色塗りAIに任せる（上流のまま）」が、いまの本線になっている。**
> [ADR-0008](0008-texture-by-hunyuan3d-paint.md) で Hunyuan3D-Paint に塗らせる方式へ
> 移った結果、UV は色塗りAI が張っている。自前の UV 工程（`bake_to_uv.py`）は
> [`experiments/`](../../experiments/README.md) へ隔離した。
>
> **ただし CONTEXT.md の「Unwrapped Shape は第一級の保存物」は残したままである。**
> 本線に UV 工程が無い以上これは実態と合っていない。
> どちらへ寄せるかは未決（[#3](https://github.com/na2-dev/parts-studio/issues/3) で扱う）。

Hunyuan3D 系の色塗りは内部で UV 展開まで行うため、上流のままだと同じ Shape に塗り直すたびに
Layout が変わり、Texture どうしに互換性が無い。Texture の作り直しと差し替えを中心に据えたいので、
形づくり・**UV展開**・色塗りの 3 工程に分け、Layout を確定させた Unwrapped Shape を第一級の
保存物とする。色塗りには「この Layout を守って画像だけ出す」ことを要求する。

展開には **xatlas**（`xatlas-python` 0.0.11・MIT）を既定に使う。チャート分割からアトラスの
詰め込みまで単体で完結し、パディングと解像度を指定できるため焼き込みにそのまま乗る。MIT なので
配布時の制約も付かない。

追記（ADR-0003 で TRELLIS.2 を採ったあと）: TRELLIS.2 の色塗りは、渡したメッシュに UV があれば
それを使い、無いときだけ自前で展開する（`trellis2/pipelines/trellis2_texturing.py:299-306`）。
つまりこの決定は上流と真っ向から戦わずに済む。ただし `run()` の中の `preprocess_mesh` が
頂点と面だけで Trimesh を作り直すため **Layout がそこで落ちる**。頂点の並びは保たれるので、
`preprocess_mesh` に visual を持ち越させる小さな手当てで足りる見込み。

## Considered Options

- **色塗りAIに任せる（上流のまま）**: 追加実装ゼロだが、Layout が毎回変わるため Texture を
  差し替え可能な資産として扱えない。本ツールの中心要求に反するので却下。
- **libigl（MPL-2.0）**: LSCM / ARAP による展開は質が高いが、アトラスへの詰め込みが付いておらず
  自前で書く必要がある。品質比較の対抗馬として後から差せるよう、展開工程は差し替え可能にしておく。
- **PyMeshLab / Blender (bpy)**: どちらも GPL。Blender の Smart UV Project は品質面で有力だが、
  別プロセス起動が前提になり、配布形態にも制約が乗る。既定にはしない。
