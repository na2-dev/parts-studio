# 実行環境は Windows ネイティブとし、CUDA 拡張はビルド済みホイールを借りる

TRELLIS.2 の README は「動作確認は Linux のみ」と書いており、CuMesh / FlexGEMM / O-Voxel /
nvdiffrast / nvdiffrec の 5 つを CUDA Toolkit でソースからビルドする必要がある。Windows で
これを自前で通すには C++ の手術（整数の縮小変換の明示キャスト、`ssize_t` / `uint` の定義追加、
無効なリテラル修正）が要る、という報告が上流の issue にある。

しかし `visualbruno/ComfyUI-Trellis2`（MIT）が **5 つとも Windows 向けにビルド済みのホイールを
配っている**（Python 3.11 / 3.13・Torch 2.7 / 2.8 / 2.10・CUDA 12.8 / 13.1、Windows 11 で動作確認済み）。
ComfyUI 本体には依存せず、このホイールだけを `pip install` して素の TRELLIS.2 を自前の
Job Server から呼ぶ。3d-studio でも kijai 氏のビルド済みホイールを同じやり方で使っていた。

WSL2 は採らない。ホイールがある以上ビルドの利点が消えるうえ、WSL2 は既定でホスト RAM の
半分しか VM に渡さないため、システム RAM 32GB の手元機ではむしろ不利になる。

メモリが足りない場合の退避路を 3 段用意しておく: bf16 → `fp8.safetensors` → GGUF
（`Aero-Ex/Trellis2-GGUF` に Q4_K_M / Q5_K_M / Q6_K / Q8_0 がある）。この GGUF 配布には
DINOv3 の重みも同梱されているため、gated の手動承認を回避する道にもなる。なお TRELLIS.2 の
「4B」は 1.3B の DiT 3 本（refiner / shape / tex）の合計であり、1 本ずつ差し替えられる。

## Consequences

借りているホイールは**第三者がビルドしたバイナリ**である。自分の機体で動かす限りは 3d-studio と
同じ運用だが、このツールを他人に配る段になったら、その時点で自前ビルドへ切り替える必要がある。
