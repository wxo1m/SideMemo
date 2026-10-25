# SideMemo code signing + trust script
# Creates (once) a self-signed code signing certificate, registers it as
# trusted on this PC, and signs SideMemo.exe and the installer.
# Must run as Administrator. Use sign_and_trust.bat for automatic elevation.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
$root = $PSScriptRoot
$certDir = Join-Path $root "certs"
if (-not (Test-Path $certDir)) { New-Item -ItemType Directory -Force -Path $certDir | Out-Null }
$pfx = Join-Path $certDir "SideMemo.pfx"
$cerPath = Join-Path $certDir "SideMemo.cer"
$pfxPassword = "sidememo"

if (Test-Path $pfx) {
  $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($pfx, $pfxPassword, [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable)
  "CERT=REUSED " + $cert.Thumbprint
} else {
  $rsa = [System.Security.Cryptography.RSA]::Create(2048)
  $req = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new("CN=SideMemo", $rsa, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
  $req.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $false))
  $req.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new([System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature, $false))
  $eku = [System.Security.Cryptography.OidCollection]::new()
  $eku.Add([System.Security.Cryptography.Oid]::new("1.3.6.1.5.5.7.3.3")) | Out-Null
  $req.CertificateExtensions.Add([System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($eku, $false))
  $newCert = $req.CreateSelfSigned([System.DateTimeOffset]::Now.AddDays(-1), [System.DateTimeOffset]::Now.AddYears(5))
  [System.IO.File]::WriteAllBytes($pfx, $newCert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx, $pfxPassword))
  [System.IO.File]::WriteAllBytes($cerPath, $newCert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))
  $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($pfx, $pfxPassword, [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable)
  "CERT=CREATED " + $cert.Thumbprint
}

$cerObj = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($cerPath)
foreach ($loc in @("CurrentUser", "LocalMachine")) {
  foreach ($storeName in @("Root", "TrustedPublisher")) {
    try {
      $locEnum = [System.Security.Cryptography.X509Certificates.StoreLocation]::$loc
      $store = [System.Security.Cryptography.X509Certificates.X509Store]::new($storeName, $locEnum)
      $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
      $store.Add($cerObj)
      $store.Close()
      "TRUST=" + $loc + "/" + $storeName + " OK"
    } catch { "TRUST=" + $loc + "/" + $storeName + " FAIL" }
  }
}

$targets = @((Join-Path $root "dist\SideMemo\SideMemo.exe"), (Join-Path $root "installer\SideMemo-Setup.exe"))
foreach ($t in $targets) {
  if (Test-Path $t) {
    $r = Set-AuthenticodeSignature -Certificate $cert -FilePath $t -HashAlgorithm SHA256
    "SIGN=" + (Split-Path -Leaf $t) + " : " + $r.Status
  } else {
    "MISSING=" + $t
  }
}

"---VERIFY---"
foreach ($t in $targets) {
  if (Test-Path $t) {
    $s = Get-AuthenticodeSignature $t
    (Split-Path -Leaf $t) + " : " + $s.Status + " / " + $s.SignerCertificate.Subject
  }
}
"DONE"
