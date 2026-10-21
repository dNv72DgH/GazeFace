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

# 以前に作った GazeFace 用の証明書があれば消す
foreach ($store in 'Root', 'TrustedPublisher') {
    Get-ChildItem "Cert:\LocalMachine\$store" | Where-Object Subject -eq $name | Remove-Item
}

# コード署名専用の証明書を作る
$cert = New-SelfSignedCertificate -Subject $name -Type CodeSigningCert `
  -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(10)

try {
    # 証明書をこの PC で信頼済みにする
    # （TrustedPublisher にも入れると、ドライバーが確認画面なしで入る）
    $cer = Join-Path $env:TEMP 'gazeface_local.cer'
    Export-Certificate -Cert $cert -FilePath $cer | Out-Null
    foreach ($store in 'Root', 'TrustedPublisher') {
        Import-Certificate -FilePath $cer -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null
    }
    Remove-Item $cer

    # GazeFace の完全版の実行ファイルと、ドライバーの catalog に署名する
    Set-AuthenticodeSignature -FilePath $exe -Certificate $cert -HashAlgorithm SHA256 | Out-Null
    Set-AuthenticodeSignature -FilePath $cat -Certificate $cert -HashAlgorithm SHA256 | Out-Null
}
finally {
    # 秘密鍵ごと削除する（以後、この証明書では何にも署名できなくなる）
    # 途中で失敗しても、鍵だけは必ず消す
    Remove-Item -LiteralPath ('Cert:\CurrentUser\My\' + $cert.Thumbprint) -DeleteKey
}

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
