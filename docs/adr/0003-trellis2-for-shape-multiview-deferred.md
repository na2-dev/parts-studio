# 形づくりは TRELLIS.2 を既定にし、多視点の条件付けは後段の課題にする

形づくりのモデルとして、多視点入力を学習済みの Hunyuan3D-2.0 mv ではなく **TRELLIS.2**
（`microsoft/TRELLIS.2`・MIT・4B）を既定に採る。造形力の差は後から埋められないが、多視点の
条件付けは後から足す余地があるため、土台の強いほうを選んだ。Hunyuan3D 系にある地域制限
（EU・英国・韓国では利用不可）も付かない。

ただし **MIT なのはコードと `microsoft/TRELLIS.2-4B` の重みまで**である。`pipeline.json` は
`briaai/RMBG-2.0`（`bria-rmbg-2.0` ライセンス・商用は BRIA との別途契約が必要）と
`facebook/dinov3-vitl16-pretrain-lvd1689m`（`dinov3-license`・手動承認の gated）を読む。
RMBG-2.0 は BiRefNet の実装で読み込まれるため、背景ぬきに限れば本家 BiRefNet（MIT）へ
差し替える余地があるが、DINOv3 は画像条件付けの中核なので差し替えられない。

代償として、**後ろの形はモデルの想像になる**。TRELLIS.2 の `run()` は画像を 1 枚しか受け取らず
（`trellis2/pipelines/trellis2_image_to_3d.py:489`）、`get_cond` が list を受け取るのはバッチ用で
多視点条件付けではない。したがって当面 front だけが Reference View で、残り 3 View は Check View
（出来上がりの検算）として使う。TRELLIS v1 が持っていた学習不要の多視点条件付けを .2 へ移植する
のが次段の課題であり、外れた場合の退避先として Hunyuan3D-2.0 mv を残す。

**2026-08-30 に手元の RTX 4070 Ti SUPER（16GB）/ RAM 31.1GB で実動を確認した**
（[実測](../measurements/2026-08-30-trellis2-memory.md)）。README の「24GB 以上」は `low_vram` を
切った経路の話で、既定の `low_vram=True` では **VRAM ピークは 2.9GB** しか使わない。
効くのはシステム RAM のほうで、**ピーク 22.2GB**。既定の最高設定 `1024_cascade` が 46 秒で通った。
ただしこの時点では DINOv3 の承認が下りておらず、同じ次元の DINOv2-large を代役にしているため、
**形の品質は未検証**である。

## Considered Options

- **Hunyuan3D-2.0 mv**: 4 枚を条件として本当に使う唯一の実績ある選択肢で、3d-studio で
  「4 枚とも絵として使う」ことを実測済み。ただし世代が古く素の造形力で劣ると見込まれ、
  ライセンスに地域制限がある。多視点移植が失敗したときの退避先として残す。
- **TRELLIS v1 の multi-image**: MIT かつ 4 枚を渡せるが、README 自身が「学習していない
  tuning-free の方式なので全ての入力で最良とは限らない」と認めている。世代も古い。
- **両方積んで並べ撮りで決める**: 最初のマイルストーンが重くなりすぎるため、既定を決めてから
  比較する順にした。
