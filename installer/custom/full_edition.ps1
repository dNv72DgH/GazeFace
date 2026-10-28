# 完全版インストール後に管理者権限で実行されるスクリプト。
# GazeFace.iss の RunFullEditionScript から呼ばれる（コンソール ウィンドウ表示あり）。
#   -AppDir   : インストール先（例: C:\Program Files\GazeFace）
#   -Language : インストーラーで選ばれた言語（ja / en）
# 終了コード 0 = 成功。0 以外ならインストーラーがエラーを表示する。
# 日本語を書く場合はこのファイルを UTF-8（BOM付き）で保存すること。
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [string]$Language = 'ja'
)
$ErrorActionPreference = 'Stop'

try {
    # ここに処理を書く
$exe  = Join-Path $AppDir 'GazeFace_UI.exe'
$cat  = Join-Path $AppDir 'driver\gfmouse.cat'   # 仮想マウス ドライバーの catalog
$name = 'CN=GazeFace Local Signing'

# 署名用の証明書は、前回のインストールで作ったものを使い回す。
# 更新のたびに作り直すと署名者が毎回変わり、Windows（アプリを署名で
# 見分ける新しいプライバシー機能）が別のアプリとみなして、カメラの
# 許可を聞き直す。許可するまでカメラが使えず、顔では許可を押せない。
# 鍵は LocalMachine\My に、取り出し不可（NonExportable）で置く。
# 読めるのは管理者と SYSTEM だけ。
$now = Get-Date
$thumbsIn = @{}
foreach ($store in 'Root', 'TrustedPublisher') {
    $thumbsIn[$store] = @(Get-ChildItem "Cert:\LocalMachine\$store" |
        Where-Object Subject -eq $name | ForEach-Object Thumbprint)
}
$cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object {
    $_.Subject -eq $name -and $_.HasPrivateKey -and
    $_.NotAfter -gt $now.AddDays(30) -and
    $thumbsIn['Root'] -contains $_.Thumbprint -and
    $thumbsIn['TrustedPublisher'] -contains $_.Thumbprint
} | Sort-Object NotAfter -Descending | Select-Object -First 1

if ($cert) {
    Write-Host "前回の証明書を使います: $($cert.Thumbprint)"
}
else {
    # 使える証明書がない（初回、または壊れている）: GazeFace 用の古い証明書と鍵を
    # すべて消してから作り直す
    foreach ($store in 'Root', 'TrustedPublisher') {
        Get-ChildItem "Cert:\LocalMachine\$store" | Where-Object Subject -eq $name | Remove-Item
    }
    Get-ChildItem Cert:\LocalMachine\My | Where-Object Subject -eq $name |
        ForEach-Object { Remove-Item -LiteralPath ('Cert:\LocalMachine\My\' + $_.Thumbprint) -DeleteKey }

    $cert = New-SelfSignedCertificate -Subject $name -Type CodeSigningCert `
      -CertStoreLocation Cert:\LocalMachine\My -KeyExportPolicy NonExportable `
      -NotAfter (Get-Date).AddYears(10)

    # 証明書をこの PC で信頼済みにする
    # （TrustedPublisher にも入れると、ドライバーが確認画面なしで入る）
    $cer = Join-Path $env:TEMP 'gazeface_local.cer'
    Export-Certificate -Cert $cert -FilePath $cer | Out-Null
    foreach ($store in 'Root', 'TrustedPublisher') {
        Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null
    }
    Remove-Item $cer
    Write-Host "証明書を作りました: $($cert.Thumbprint)"
}

# GazeFace の完全版の実行ファイルと、ドライバーの catalog に署名する
Set-AuthenticodeSignature -FilePath $exe -Certificate $cert -HashAlgorithm SHA256 | Out-Null
Set-AuthenticodeSignature -FilePath $cat -Certificate $cert -HashAlgorithm SHA256 | Out-Null

# 結果を確認する（Valid 以外は失敗として catch へ）
foreach ($f in $exe, $cat) {
    $status = (Get-AuthenticodeSignature $f).Status
    Write-Host "署名の状態: $(Split-Path -Leaf $f) = $status"
    if ($status -ne 'Valid') {
        throw "署名を確認できませんでした（$(Split-Path -Leaf $f): $status）"
    }
}

# 仮想マウス ドライバーをインストールする（失敗すると throw → catch へ）
& (Join-Path $AppDir 'driver\gfmouse_setup.ps1')
    exit 0
}
catch {
    # エラー内容はファイルに残し、インストーラーのエラー画面に表示させる
    Write-Host ''
    Write-Host "エラー: $_" -ForegroundColor Red
    Set-Content -LiteralPath (Join-Path $PSScriptRoot 'full_edition_error.txt') `
      -Value "$_" -Encoding UTF8
    exit 1
}
