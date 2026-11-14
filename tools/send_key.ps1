# Focus the Unicorn Overlord window and send real key presses to it.
#
# The game reads keys with GetAsyncKeyState, which sees the physical keyboard state, so keys sent with
# SendInput work as long as the game window is in front (ikb_foreground checks GetForegroundWindow).
#
#   .\send_key.ps1 -Keys e,e,e -DelayMs 700
#   .\send_key.ps1 -Keys left,left,e -DelayMs 400
param(
    [string[]]$Keys = @('e'),
    [int]$DelayMs = 700,
    [string]$Title = 'Unicorn Overlord'
)

Add-Type -Namespace Uo -Name Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
'@

$proc = Get-Process uo -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $proc) { Write-Error 'uo.exe is not running with a window'; exit 1 }
$h = $proc.MainWindowHandle
[Uo.Native]::ShowWindow($h, 9) | Out-Null
[Uo.Native]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 400
if ([Uo.Native]::GetForegroundWindow() -ne $h) {
    Write-Warning 'the game window is not in front; keys may be ignored'
}

$codes = @{ 'e' = 0x45; 'q' = 0x51; 'w' = 0x57; 'a' = 0x41; 's' = 0x53; 'd' = 0x44; 'x' = 0x58; 'z' = 0x5A;
            'f' = 0x46; 'r' = 0x52; 'c' = 0x43; 'm' = 0x4D; 'tab' = 0x09; 'space' = 0x20; 'enter' = 0x0D;
            'esc' = 0x1B; 'left' = 0x25; 'up' = 0x26; 'right' = 0x27; 'down' = 0x28;
            '1' = 0x31; '2' = 0x32; '3' = 0x33; '4' = 0x34; 'shift' = 0x10; 'ctrl' = 0x11 }

foreach ($k in $Keys) {
    $name = $k.ToLower()
    if (-not $codes.ContainsKey($name)) { Write-Warning "unknown key $k"; continue }
    $vk = [byte]$codes[$name]
    [Uo.Native]::keybd_event($vk, 0, 0, [UIntPtr]::Zero)          # down
    Start-Sleep -Milliseconds 80
    [Uo.Native]::keybd_event($vk, 0, 2, [UIntPtr]::Zero)          # up
    Start-Sleep -Milliseconds $DelayMs
}
Write-Output "sent $($Keys.Count) key(s) to window $h"
