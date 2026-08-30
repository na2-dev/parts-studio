# TRELLIS.2 は RTX 4070 Ti SUPER (16GB) / RAM 31.1GB で動くか（2026-08-30 実測）

## なぜ測ったか

ADR-0003 と ADR-0004 は「TRELLIS.2 がこの機体で動く」という**未検証の前提**の上に立っていた。
上流 README は「24GB 以上の GPU が必要」と書いており、手元は 16GB。さらに上流 issue には
「low_vram でも大量のシステム RAM が要る（報告例は 128GB）」とあり、手元は 31.1GB。
ここが崩れると UI の設計も工程の分け方も作り直しになるので、最初に試した。

DINOv3 の利用承認が下りていなかったため、**同じ隠れ次元 1024 を持つ `facebook/dinov2-large` を
代役**に立てた（キャッシュ済み・非 gated）。特徴の分布が違うので**出来上がる形は絵に似ない**が、
「落ちるか」「何 GB 使うか」「何秒かかるか」は測れる。

最初は乱数を条件に与えたが、工程1（疎構造）が空のボクセルを返して工程2で落ちた
（`sparse/basic.py:463` で `coords` が 0 件）。**条件が壊れていると空の形が出る**という挙動自体、
覚えておく価値がある。

## 条件

| | |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER 16376 MiB / driver 610.88 |
| システム RAM | 31.1 GB |
| Python / torch | 3.11.9 / 2.7.0+cu128 |
| attention | xformers 0.0.30（dense・sparse とも） |
| `PYTORCH_CUDA_ALLOC_CONF` | `expandable_segments:True` |
| 入力 | 512x512 の透過 PNG（頭・胴・腕を矩形と楕円で描いた合成画像） |
| `low_vram` | True（`pipeline.json` に指定が無く、既定が True） |

## 結果

| 経路 | 所要 | VRAM ピーク | VRAM 確保 | RAM ピーク | 出力 |
|---|---|---|---|---|---|
| `512` | 84s | **2.84 GB** | 3.14 GB | 22.18 GB | 頂点 60,452 / 面 110,096 |
| `1024_cascade`（既定） | **46s** | **2.86 GB** | 3.52 GB | 21.64 GB | 頂点 195,698 / 面 340,994 |

`from_pretrained` は 57 秒かかり、その時点で RAM を 16.7〜19.4 GB 使う。

## 分かったこと

**1. VRAM は問題にならない。ピークで 2.9 GB しか使わない。**
README の「24GB 以上」は `low_vram` を切った経路の話。既定の `low_vram=True` では 8 つのモデルを
CPU 上に置き、工程ごとに必要な分だけ GPU へ出し入れする。**16GB は余裕どころか過剰**で、
8GB 機でも VRAM 面では通る可能性が高い。

**2. 効くのはシステム RAM で、ピーク 22.2 GB。**
31.1GB からこれを引くと残り約 9GB。Windows の取り分を考えると通るが、**余裕は大きくない**。
他の重いアプリを立ち上げたまま走らせるのは避けたほうがよい。

**3. `1024_cascade` のほうが速い。**
512 の 84 秒に対し 46 秒。512 の測定が先だったので、初回のカーネル準備などが 512 側に乗った
と見られる。いずれにせよ**既定の最高設定を落とす理由は無い**。

**4. RAM の実測値に 2.7GB のばらつきがある。**
`from_pretrained` 直後が 19.41 GB（512 の回）と 16.70 GB（1024_cascade の回）で違う。
読み込むモデルは `pipeline_type` に関わらず同じはずなので、safetensors のメモリマップが
どれだけ常駐扱いになるかの差と見ている。**RSS は上限の証明にはならない**ので、
余裕を 3GB 程度は見ておく。

## 測っていないこと

- **形の品質は一切測っていない。** DINOv2 を代役にしているので、出来た形は入力の絵に似ない。
  品質の判定は DINOv3 の承認が下りてからやり直す
- 8GB 機で通るかは試していない（VRAM 面では通りそう、という推測にとどまる）
- テクスチャ工程は `run()` の中で走っているが、出来た絵の質は見ていない
- 連続実行時のメモリの積み上がり（上流 issue に「VRAM leak」「batch 生成で異常増加」の報告がある）

## 再現方法

`TRELLIS.2/memtest.py`（git 管理外）:

```powershell
cd C:\work\parts-studio\TRELLIS.2
$env:ATTN_BACKEND = "xformers"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"
..\venv\Scripts\python.exe memtest.py 1024_cascade
```
