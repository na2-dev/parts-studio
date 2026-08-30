# Windows 機に SSH で入れるようにする（同一 LAN 前提）

Mac（開発）から Windows 機（GPU 実行）へ、公開鍵認証で直接コマンドを流せるようにする手順。
ルーターのポート開放はしない。LAN の外からは到達できないままにする。

Mac 側の情報:

- IP: `192.168.3.5`
- 使う秘密鍵: `~/.ssh/win.key`（RSA 2048・パスフレーズなし・`SHA256:zpwq2LYxvoeLAXXAhkyEvJjhepHPsYm7/0IbSwSO5H4`）

**★ `~/.ssh/win_public.key` はそのまま使えない。** これは PEM（X.509 SubjectPublicKeyInfo・
`-----BEGIN PUBLIC KEY-----`）形式で、`authorized_keys` が要求する OpenSSH の一行形式ではない。
貼っても認証は通らず、しかも**エラーは出ずにパスワードを聞かれ続ける**形で現れる。

`win.key` から一行形式を導出して使う:

```sh
ssh-keygen -y -f ~/.ssh/win.key
```

導出結果（これを Windows 側に登録する）:

```
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDKek/IN4XsrIHXQXMpkH13/UjjUKV9olYcuao1Xzqt+8s5CC6snPunb2aUh2cMkBquf1kc/fZBWW5+kWJxwIQbtuhAAGioMdMbrZDmndAAYDGVtVwtkCztRN8jCMCltQDE0zQAcDYUyckdqKRPpOMtuNhG73+ymD6MrrefIPYeCg5wMAqLH3hcqpZK0Pz6oXi8qPxvK/lCrhteOYASXQ1lOfm+t+9dhVOK7Av+wYI8goo7m0n4j445J3FdyAQxSPnHWO5JEItiG0e4LtbUbUCP9/xcoq9ci5mM2bPFUsXhgI8nt22bBwhnJMGDez12KcCyAm+T85hY1xqmQWTNj5Wd
```

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

出力を Mac 側に伝える。IP・ユーザー名がこの先の接続に要る。

## 手順3: 既定シェルを PowerShell にする（Windows 側・管理者）

これをやらないと SSH 越しのシェルが `cmd` になり、扱いにくい。

```powershell
# PowerShell 7 が入っている場合
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Program Files\PowerShell\7\pwsh.exe" -PropertyType String -Force

# 入っていない場合（Windows 標準の PowerShell 5.1）
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
```

## 手順4: 公開鍵を登録する（Windows 側・管理者）

**★ここが最大の落とし穴。** ログインするユーザーが管理者グループに属している場合、
Windows の OpenSSH は `C:\Users\<user>\.ssh\authorized_keys` を**見ない**。
`C:\ProgramData\ssh\administrators_authorized_keys` のほうを読む。
さらに、このファイルの ACL を Administrators と SYSTEM だけに絞らないと、
**エラーを出さずに公開鍵認証が失敗する**（パスワードを聞かれ続ける形で現れる）。

```powershell
$key = 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDKek/IN4XsrIHXQXMpkH13/UjjUKV9olYcuao1Xzqt+8s5CC6snPunb2aUh2cMkBquf1kc/fZBWW5+kWJxwIQbtuhAAGioMdMbrZDmndAAYDGVtVwtkCztRN8jCMCltQDE0zQAcDYUyckdqKRPpOMtuNhG73+ymD6MrrefIPYeCg5wMAqLH3hcqpZK0Pz6oXi8qPxvK/lCrhteOYASXQ1lOfm+t+9dhVOK7Av+wYI8goo7m0n4j445J3FdyAQxSPnHWO5JEItiG0e4LtbUbUCP9/xcoq9ci5mM2bPFUsXhgI8nt22bBwhnJMGDez12KcCyAm+T85hY1xqmQWTNj5Wd'
$f   = 'C:\ProgramData\ssh\administrators_authorized_keys'

Add-Content -Path $f -Value $key -Encoding utf8
icacls $f /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F'
Restart-Service sshd
```

管理者グループに属していない一般ユーザーで入るなら、代わりに:

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.ssh" | Out-Null
Add-Content -Path "$env:USERPROFILE\.ssh\authorized_keys" -Value $key -Encoding utf8
```

## 手順5: スリープしないようにする（Windows 側・管理者）

寝ている機体には入れない。GPU ジョブは長いので、画面だけ消して本体は起こしておく。

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10
```

## 手順6: Mac 側から繋ぐ

```sh
ssh <ユーザー名>@<IP>
```

入れたら `~/.ssh/config` に登録する（この作業は Mac 側で行う）。

```
Host gpu
    HostName 192.168.3.xxx
    User <ユーザー名>
    IdentityFile ~/.ssh/win.key
    IdentitiesOnly yes
    ServerAliveInterval 30
```

`IdentitiesOnly yes` は必須。`~/.ssh/` に鍵が数十本あるため、これが無いと SSH が別の鍵から順に
試し、Windows 側が試行回数の上限で接続を切る（`Too many authentication failures`）。

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

## うまくいかないときの見どころ

| 症状 | 原因 |
|---|---|
| 公開鍵を置いたのにパスワードを聞かれる | 手順4 の ACL（`icacls`）を忘れている。または管理者なのに `authorized_keys` のほうに置いた |
| `Connection refused` | `sshd` が起動していない（`Get-Service sshd`）、または IP が変わった |
| 接続が固まる | Windows がスリープした（手順5） |
| ログインすると `cmd` が出る | 手順3 の `DefaultShell` が未設定 |

sshd 側のログ:

```powershell
Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 30 | Format-List TimeCreated, Message
```
