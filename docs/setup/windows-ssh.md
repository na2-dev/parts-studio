# Windows 機に SSH で入れるようにする（同一 LAN 前提）

Mac（開発）から Windows 機（GPU 実行）へ、公開鍵認証で直接コマンドを流せるようにする手順。
ルーターのポート開放はしない。LAN の外からは到達できないままにする。

## 接続先（実測済み）

| | 値 | 取得元 |
|---|---|---|
| ホスト名 | `natsu` | `hostname` |
| IP | `192.168.3.6` | `Get-NetIPAddress` |
| ログインユーザー | `k2187`（`whoami` は `natsu\k2187`） | `whoami` |
| システム RAM | **31.1 GB** | `Win32_ComputerSystem` |
| GPU | **NVIDIA GeForce RTX 4070 Ti SUPER** ＋ AMD Radeon(TM) Graphics（CPU内蔵） | `Win32_VideoController` |
| sshd | `OpenSSH_for_Windows_9.5` | Mac から `ssh -v` |

Mac 側は `192.168.3.5`。

内蔵GPUが載っているので、**画面表示を内蔵GPUに逃がせば 4070 Ti SUPER の 16GB をほぼ全部
AI に回せる**。3d-studio では「Windows が画面表示に使う VRAM」が VRAM 判定を曇らせていたが、
この機体はそれを切り離せる。

## 鍵

- 秘密鍵: `~/.ssh/win.key`（RSA 2048・パスフレーズなし・`SHA256:zpwq2LYxvoeLAXXAhkyEvJjhepHPsYm7/0IbSwSO5H4`）

**`~/.ssh/win_public.key` は authorized_keys に使えない。** これは PEM
（X.509 SubjectPublicKeyInfo・`-----BEGIN PUBLIC KEY-----`）形式で、`authorized_keys` が要求する
OpenSSH の一行形式ではない。`win.key` から導出して使う:

```sh
ssh-keygen -y -f ~/.ssh/win.key
```

導出結果（これを Windows 側に登録する。**改行を入れずに1行で**）:

```
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDKek/IN4XsrIHXQXMpkH13/UjjUKV9olYcuao1Xzqt+8s5CC6snPunb2aUh2cMkBquf1kc/fZBWW5+kWJxwIQbtuhAAGioMdMbrZDmndAAYDGVtVwtkCztRN8jCMCltQDE0zQAcDYUyckdqKRPpOMtuNhG73+ymD6MrrefIPYeCg5wMAqLH3hcqpZK0Pz6oXi8qPxvK/lCrhteOYASXQ1lOfm+t+9dhVOK7Av+wYI8goo7m0n4j445J3FdyAQxSPnHWO5JEItiG0e4LtbUbUCP9/xcoq9ci5mM2bPFUsXhgI8nt22bBwhnJMGDez12KcCyAm+T85hY1xqmQWTNj5Wd
```

---

## ★ 黙って失敗する罠が3つある

公開鍵認証の失敗は、**どれもエラーメッセージを出さない**。
Mac 側には一律 `Permission denied (publickey,password,keyboard-interactive)` としか出ないので、
原因は Windows 側で切り分けるしかない。

| # | 罠 | 対策 |
|---|---|---|
| 1 | **PEM 形式の公開鍵を貼った** | `ssh-keygen -y -f ~/.ssh/win.key` の一行形式を使う |
| 2 | **置き場所が違う** — 管理者グループのユーザーは `~/.ssh/authorized_keys` が**読まれない**。`C:\ProgramData\ssh\administrators_authorized_keys` のほうが読まれる | 手順4-a で所属を確認してから置く |
| 3 | **BOM が入った** — Windows PowerShell 5.1 の `Add-Content -Encoding utf8` は **BOM 付き**で書く。1行目が `<BOM>ssh-rsa ...` になり鍵として読めない | `Add-Content` を使わず `[System.IO.File]::WriteAllText` で BOM なし固定にする |
| 4 | **`DefaultShell` に存在しないパスを設定した** — 手順3 で PowerShell 7 のパスを入れたのに PS7 が入っていない、など。sshd は**鍵を見る前にログインを拒否する**ので、鍵の問題と区別がつかない | 実在するパスを設定する。実際に踏んだ（下記） |

加えて、`administrators_authorized_keys` は ACL を Administrators と SYSTEM だけに絞らないと
無視される（これも無言）。

罠4 は実際に踏んだ。Windows 側のログにだけ理由が出た:

```
sshd: User k2187 not allowed because shell c://program files//powershell//7//pwsh.exe does not exist
sshd: Connection closed by invalid user k2187 192.168.3.5 port 50871
```

**この4つはどれも Mac 側では同じ `Permission denied (publickey,password,keyboard-interactive)` に
なる。** 鍵まわりを疑う前に、まず Windows 側のログを見ること。

---

## 手順1: OpenSSH サーバーを入れる（Windows 側・管理者 PowerShell）

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

ファイアウォール規則 `OpenSSH-Server-In-TCP` は上のインストールで自動作成される。念のため確認:

```powershell
Get-NetFirewallRule -Name *OpenSSH-Server* | Select-Object Name, Enabled, Direction, Action
```

## 手順2: 接続先の情報を控える（Windows 側）

```powershell
hostname
(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '192.168.*' }).IPAddress
whoami
[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
(Get-CimInstance Win32_VideoController).Name
```

結果は冒頭の表に記録済み。

## 手順3: 既定シェルを PowerShell にする（Windows 側・管理者）

これをやらないと SSH 越しのシェルが `cmd` になり、扱いにくい。

**★存在しないパスを入れると、sshd は鍵を見る前にログインを拒否する（罠4）。**
この機体には PowerShell 7 が入っていないので、標準の 5.1 を指す。

```powershell
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
```

`-Force` は既存の値があれば**上書き**する。間違ったパスを設定してしまった場合の直しにも、
同じコマンドをそのまま使える（`Set-ItemProperty` を使っても結果は同じ）。

設定したパスが実在するか、その場で確かめる。**`True` が出ること**:

```powershell
Test-Path (Get-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell).DefaultShell
Restart-Service sshd
```

PowerShell 7 を使いたい場合は、**先に入れてから**パスを差し替える（`winget install Microsoft.PowerShell`）。

## 手順4: 公開鍵を登録する（Windows 側・管理者）

### 4-a. まず所属グループを確かめる

置き場所がこれで変わる。`1` 以上なら管理者、`0` なら一般ユーザー。

```powershell
((whoami /groups) -match 'S-1-5-32-544' | Measure-Object).Count
```

### 4-b. 管理者だった場合

**丸ごと貼り付けること。** `$key` の行に改行が入ると壊れる。

```powershell
$key = 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDKek/IN4XsrIHXQXMpkH13/UjjUKV9olYcuao1Xzqt+8s5CC6snPunb2aUh2cMkBquf1kc/fZBWW5+kWJxwIQbtuhAAGioMdMbrZDmndAAYDGVtVwtkCztRN8jCMCltQDE0zQAcDYUyckdqKRPpOMtuNhG73+ymD6MrrefIPYeCg5wMAqLH3hcqpZK0Pz6oXi8qPxvK/lCrhteOYASXQ1lOfm+t+9dhVOK7Av+wYI8goo7m0n4j445J3FdyAQxSPnHWO5JEItiG0e4LtbUbUCP9/xcoq9ci5mM2bPFUsXhgI8nt22bBwhnJMGDez12KcCyAm+T85hY1xqmQWTNj5Wd'
$f   = 'C:\ProgramData\ssh\administrators_authorized_keys'

[System.IO.File]::WriteAllText($f, $key + "`n", (New-Object System.Text.UTF8Encoding($false)))
icacls $f /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F'
Restart-Service sshd
```

### 4-c. 一般ユーザーだった場合

```powershell
$d = "$env:USERPROFILE\.ssh"
New-Item -ItemType Directory -Force -Path $d | Out-Null
[System.IO.File]::WriteAllText("$d\authorized_keys", $key + "`n", (New-Object System.Text.UTF8Encoding($false)))
Restart-Service sshd
```

### 4-d. 書けたか確かめる

```powershell
$f = 'C:\ProgramData\ssh\administrators_authorized_keys'   # 一般ユーザーなら "$env:USERPROFILE\.ssh\authorized_keys"

# 先頭バイト: 239 187 191 で始まっていたら BOM 混入（罠3）
[System.IO.File]::ReadAllBytes($f)[0..5]

# 行数と各行の長さ: 1行 / 380前後 になるはず。2行以上なら改行が混入している
(Get-Content $f) | ForEach-Object { $_.Length }

# ACL: Administrators と SYSTEM だけになっているか
icacls $f
```

## 手順5: スリープしないようにする（Windows 側・管理者）

寝ている機体には入れない。GPU ジョブは長いので、画面だけ消して本体は起こしておく。

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10
```

## 手順6: Mac 側の設定（Mac 側・設定済み）

`~/.ssh/config` の**先頭**に置く。`Host *` より後ろに書くと、そちらの `IdentityFile ~/.ssh/id_rsa`
が先に効いてしまう。

```
Host gpu
    HostName 192.168.3.6
    User k2187
    IdentityFile ~/.ssh/win.key
    IdentitiesOnly yes
    ServerAliveInterval 30
```

`IdentitiesOnly yes` は必須。`~/.ssh/` に鍵が30本近くあるため、無いと SSH が別の鍵から順に試し、
Windows 側が試行回数の上限で接続を切る（`Too many authentication failures`）。

以後 `ssh gpu` で入れる。

## 手順7（任意）: パスワード認証を切る

公開鍵で入れることを**確認してから**行う。順番を間違えると締め出される。

`C:\ProgramData\ssh\sshd_config` を管理者権限で開き、以下にする。

```
PubkeyAuthentication yes
PasswordAuthentication no
```

```powershell
Restart-Service sshd
```

---

## 切り分け

### Mac 側から（どこまで届いているか）

```sh
ping -c 3 192.168.3.6                 # 疎通
nc -z -v 192.168.3.6 22               # ポート22が開いているか
ssh -vv -o BatchMode=yes gpu exit 2>&1 | grep -E "Offering|Trying private|Authentications"
```

`Trying private key: .../win.key` の直後に `Authentications that can continue:` が返るなら、
**鍵は届いていて Windows 側に拒否されている**。上の罠1〜3 のどれか。

### Windows 側から（なぜ拒否したか）

決定的な理由はここに出る。

```powershell
Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 30 | Format-List TimeCreated, Message
```

### 症状と原因の対応

| 症状 | 原因 |
|---|---|
| 公開鍵を置いたのにパスワードを聞かれる／`Permission denied (publickey,...)` | 罠1〜3 のいずれか、または ACL 未設定 |
| `No route to host`（初回だけ） | ARP 未解決。もう一度実行すれば通ることが多い |
| `Connection refused` | `sshd` が起動していない（`Get-Service sshd`）、または IP が変わった |
| `Too many authentication failures` | Mac 側 `IdentitiesOnly yes` が無い |
| 接続が固まる | Windows がスリープした（手順5） |
| ログインすると `cmd` が出る | 手順3 の `DefaultShell` が未設定 |

---

## 手順8: 出力の文字化けを直す（Windows 側・実施済み）

既定のままだと PowerShell の出力が Shift-JIS で返り、Mac 側で日本語のエラーメッセージが
読めなくなる。プロファイルで UTF-8 に固定する。

`C:\Users\k2187\Documents\WindowsPowerShell\profile.ps1` に以下を置いた（**BOM なし**）:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
```

Mac 側から置く場合:

```sh
printf '%s\r\n' '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8' \
                '$OutputEncoding = [System.Text.Encoding]::UTF8' > /tmp/ps_profile.ps1
ssh gpu 'New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\Documents\WindowsPowerShell" | Out-Null'
scp /tmp/ps_profile.ps1 gpu:'C:/Users/k2187/Documents/WindowsPowerShell/profile.ps1'
```

---

## 繋がった時点の環境（実測）

```
natsu / natsu\k2187 / PowerShell 5.1.26100.9278
NVIDIA GeForce RTX 4070 Ti SUPER — 16376 MiB（起動時の使用 1229 MiB）/ ドライバ 610.88
```

| | 状態 |
|---|---|
| Python | 3.10 / 3.11 / 3.12 / 3.13 が導入済み。`python` は 3.10 を指す |
| CUDA Toolkit（`nvcc`） | **未導入** |
| conda | **未導入** |
| git | 2.50.1.windows.1 |
| ディスク | C: 空き 1105GB / Z: 空き 2638GB |
| RAM | 31.1GB（調査時の空き 16.8GB） |

ADR-0004 の方針（ビルド済みホイールを借りる）なら CUDA Toolkit は不要。上流が動作確認している
**Python 3.11 ＋ Torch 2.7.0+cu128** の組み合わせがそのまま作れる。

## 現状

手順1〜8 すべて完了。Mac から `ssh gpu` で入れる。
