# Windows 機での起動手順（B-4）

環境が全部そろった Windows 機（GPU 機）で、**ツールを使える状態にする手順**。
環境そのものの作り方は各文書へ（下の表）。

## 前提: 何がそろっている必要があるか

| もの | 作り方 | 確かめかた |
| :--- | :--- | :--- |
| リポジトリ `C:\work\parts-studio` | [trellis2-windows.md](trellis2-windows.md) の手順1 | `dir C:\work\parts-studio\tools` |
| `venv/`（3.11・形づくり） | 同・手順2〜6 | `venv\Scripts\python.exe -c "import torch"` |
| `venv-bpy/`（リトポロジー） | 同・手順7 | `venv-bpy\Scripts\python.exe -c "import bpy"` |
| `TRELLIS.2/` と `_wheels/` | 同・手順1 | — |
| 塗り環境（借り物 `Z:\work\3d-studio`） | [paint-environment.md](paint-environment.md) | `dir Z:\work\3d-studio\venv-21` |
| HF の同意（DINOv3） | [trellis2-windows.md](trellis2-windows.md) の手順6 | 初回の形づくりが通る |

## 起動する

### いちばん簡単: `start_server.cmd` をダブルクリック

リポジトリ直下の [`start_server.cmd`](../../start_server.cmd) がジョブサーバーを立てる。
ブラウザで **`http://127.0.0.1:8787/`** を開くと UI が出る
（使いかたは [job-server.md](job-server.md)）。

サーバーのログは `out\server.log`、Job ごとのログは `out\jobs\<id>\log.txt`。

### PC を点けたら勝手に立つようにする（タスクスケジューラ）

一度だけ、PowerShell で（このユーザーの ssh セッションからは昇格なしで登録できた）:

```powershell
schtasks /Create /F /TN parts-studio-server /SC ONSTART /RU <ユーザー名> `
  /TR "C:\work\parts-studio\start_server.cmd"
```

すぐ立てるなら `schtasks /Run /TN parts-studio-server`。
やめるなら `schtasks /Delete /F /TN parts-studio-server`。

**2026-09-03 に実機で確認したのは「登録」と「`/Run` で立つ」まで**
（`/Run` → 別のセッションから `http://127.0.0.1:8787/jobs` が 200、
ログも UTF-8 で出る）。**再起動で ONSTART が発火することは確認していない。**
パスワードを保存しない登録（`/RP` なし）は「ログオン時のみ実行」の扱いに
なることがあり、その場合は起動直後ではなくログオン後に立つ。
再起動したら、まずブラウザで開いてみて、立っていなければ
`schtasks /Run /TN parts-studio-server` を打つこと。

### ★ssh 越しに立てても、切ると死ぬ

`ssh` で入って `Start-Process` などで立てたサーバーは、**ssh セッションが
閉じると一緒に落ちる**（Windows の sshd がジョブごと片付ける。2026-09-03 実測）。
遠隔から立てたいときも、上のタスクスケジューラ経由で
`schtasks /Run /TN parts-studio-server` を打つこと（これはセッションが閉じても残る）。

## 別の機体（Mac など）のブラウザから使う

サーバーは `127.0.0.1` しか聴かない（認証が無いため。
[job-server.md](job-server.md)）。SSH のポートフォワードで届かせる:

```
ssh -N -L 8787:127.0.0.1:8787 gpu
```

そのあと手元のブラウザで `http://127.0.0.1:8787/` を開く。

## 止める・困ったとき

| したいこと / 症状 | やること |
| :--- | :--- |
| 止める | `taskkill /F /FI "IMAGENAME eq python.exe"`（★他の Python も全部落ちる。**UI が空でも安全とは限らない** — サーバーを立て直した直後は前の Job が UI から見えないため。`tasklist /FI "IMAGENAME eq python.exe"` で本数を見てから） |
| ポートが塞がっている | `netstat -ano \| findstr :8787` で PID を見て `taskkill /F /PID <pid>` |
| Job が `running` のまま固まって見える | サーバーを立て直した直後なら、前の Job は API から見えない（[job-server.md](job-server.md)）。`out\jobs\<id>\log.txt` を直接見る |
| 形づくりで DINOv3 の 401 | HF の同意とトークン（`setx HF_TOKEN ...`）。[trellis2-windows.md](trellis2-windows.md) |
| 塗りで「塗り環境が見つかりません」 | `Z:` がつながっているか。[paint-environment.md](paint-environment.md) |

## ★配るときの注意: ビルド済みホイールを借りている

この環境は **CUDA 拡張をソースからビルドしていない**（ADR-0004）。

| 借りているもの | 出どころ | 対象 |
| :--- | :--- | :--- |
| `cumesh` / `o_voxel` / `flex_gemm` / `nvdiffrast` / `nvdiffrec_render` / `custom_rasterizer`（※） | [visualbruno/ComfyUI-Trellis2](https://github.com/visualbruno/ComfyUI-Trellis2) の `wheels/`（`_wheels/` に clone） | 形づくり（venv・cp311・torch 2.7.0+cu128） |
| `custom_rasterizer` | [kijai/ComfyUI-Hunyuan3DWrapper](https://github.com/kijai/ComfyUI-Hunyuan3DWrapper) の `wheels/` | 塗り（venv-21・cp312・torch 2.6.0+cu126） |

※ venv 側の `custom_rasterizer` は一括 install で入るが TRELLIS.2 は使わない。
**使っていなくても第三者ビルドが入っている**ので、棚卸しからは外さない。

つまり:

- **Python・torch・CUDA の版が1つでも違う機体では、この手順のままでは動かない**
  （ホイールの名前が示す組み合わせに固定されている）
- **他人に配る・別の構成で動かすなら、これらの拡張を自前でビルドする必要がある**
  （CUDA Toolkit と MSVC が要る。ここではやっていないので手順も無い）
- 借り物のホイールは第三者のビルドである。**配布物に同梱する前に、
  出どころとライセンスを自分で確かめること**
- GPU の世代にも依存する。`custom_rasterizer` は sm_86（RTX 3070 など）向けが
  入っておらず動かない（[実測](paint-environment.md)）。動作実績があるのは
  sm_89（RTX 4070 Ti SUPER）だけ
