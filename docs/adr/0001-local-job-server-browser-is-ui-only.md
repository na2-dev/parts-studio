# 推論は利用者のPC上のジョブサーバーで走らせ、ブラウザはUIだけにする

Meshy / Tripo のような Web ブラウザツールを目指すが、形づくりの本命である Hunyuan3D-2.0 mv は CUDA C++ 拡張（`custom_rasterizer`）と 7GB 超の VRAM を要求し、WebGPU / ONNX Runtime Web への移植パスが無い。よってブラウザ内推論は採らず、利用者の PC で Python+CUDA のジョブサーバーを動かし、ブラウザは「ジョブを投げて成果物を受け取る・表示する」だけを担当する。想定 GPU は RTX 4070 Ti SUPER（16GB）。

ジョブ投入は HTTP API 境界として切り、GPU がローカルか遠隔かをブラウザ側に持ち込まない。将来 GPU サーバー方式（Meshy 型）に切り替えるときは、この API の向き先を変えるだけで済むようにする。

## Considered Options

- **ブラウザ内推論（WebGPU / ONNX Runtime Web）**: インストール不要で最も「Web ツールらしい」が、Hunyuan3D 系は CUDA 拡張依存のため移植不能と判断して却下。
- **共有GPUサーバー方式**: 利用者に GPU を要求しない Meshy/Tripo 本来の形。最終形としては望ましいが、GPU 機の常時稼働と配信コストが最初から乗るため、初手では採らない。API 境界を残すことで後から移行可能にした。
