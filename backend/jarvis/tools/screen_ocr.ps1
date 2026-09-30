# Screen capture + OCR using the OCR engine built into Windows 10/11
# (Windows.Media.Ocr). No third-party dependency, no network call.
#
# Usage:
#   screen_ocr.ps1                 -> OCR the whole virtual screen
#   screen_ocr.ps1 -Hwnd 12345     -> OCR just that window's rectangle
#   screen_ocr.ps1 -Path shot.png  -> OCR an existing image file
#   -Save <path>                   -> also keep the captured PNG
#
# Emits a single JSON object on stdout:
#   {"ok":true,"text":"...","width":W,"height":H,
#    "lines":[{"text":"...","words":[{"text":"..","x":..,"y":..,"w":..,"h":..}]}]}
# On failure: {"ok":false,"error":"..."}  (exit code 2)

[CmdletBinding()]
param(
    [int]$Hwnd = 0,
    [string]$Path = "",
    [string]$Save = ""
)

$ErrorActionPreference = "Stop"

# Emit UTF-8 so non-ASCII on screen (Urdu, symbols, accents) survives the pipe.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Fail($msg) {
    [Console]::Out.Write((@{ ok = $false; error = "$msg" } | ConvertTo-Json -Compress))
    exit 2
}

# --- offset of the captured region, so coordinates come back screen-absolute --
$originX = 0
$originY = 0

try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    Add-Type -AssemblyName System.Runtime.WindowsRuntime

    # ---------------------------------------------------------------- capture
    $imagePath = $Path
    $temp = ""
    if (-not $imagePath) {
        if ($Hwnd -ne 0) {
            # Capture one window's rectangle.
            if (-not ("Win32Rect" -as [type])) {
                Add-Type -Namespace Native -Name Win32Rect -MemberDefinition @"
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
[DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
"@
            }
            $h = [IntPtr]$Hwnd
            if (-not [Native.Win32Rect]::IsWindow($h)) { Fail "Window $Hwnd no longer exists." }
            $r = New-Object Native.Win32Rect+RECT
            [void][Native.Win32Rect]::GetWindowRect($h, [ref]$r)
            $w = $r.Right - $r.Left
            $hgt = $r.Bottom - $r.Top
            if ($w -le 0 -or $hgt -le 0) { Fail "Window $Hwnd is minimized or has no visible area." }
            $originX = $r.Left
            $originY = $r.Top
            $bmp = New-Object System.Drawing.Bitmap($w, $hgt)
            $g = [System.Drawing.Graphics]::FromImage($bmp)
            $g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
        }
        else {
            $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
            $originX = $b.X
            $originY = $b.Y
            $bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
            $g = [System.Drawing.Graphics]::FromImage($bmp)
            $g.CopyFromScreen($b.X, $b.Y, 0, 0, $bmp.Size)
        }
        $temp = if ($Save) { $Save } else { Join-Path $env:TEMP ("jarvis_ocr_" + [guid]::NewGuid().ToString("N") + ".png") }
        $bmp.Save($temp, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        $imagePath = $temp
    }
    if (-not (Test-Path $imagePath)) { Fail "Image not found: $imagePath" }

    # ------------------------------------------------------------------- OCR
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]

    # WinRT async -> synchronous bridge.
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    function Await($op, $type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
        $task.Wait()
        $task.Result
    }

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) { Fail "No OCR language pack is installed for your user profile." }

    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = @()
    foreach ($line in $result.Lines) {
        $words = @()
        foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            $words += @{
                text = $word.Text
                x    = [int]($r.X + $originX)
                y    = [int]($r.Y + $originY)
                w    = [int]$r.Width
                h    = [int]$r.Height
            }
        }
        $lines += @{ text = $line.Text; words = $words }
    }

    $payload = @{
        ok     = $true
        text   = $result.Text
        width  = [int]$decoder.PixelWidth
        height = [int]$decoder.PixelHeight
        lines  = $lines
        image  = if ($Save) { $Save } else { "" }
    }

    $stream.Dispose()
    if ($temp -and -not $Save) { Remove-Item $temp -ErrorAction SilentlyContinue }

    [Console]::Out.Write(($payload | ConvertTo-Json -Depth 6 -Compress))
}
catch {
    Fail $_.Exception.Message
}
