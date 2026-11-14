@echo off
setlocal
set "UO_GPU=vulkan"
set "UO_VK_RECORDER=0"
set "UO_SHARED_FONTS=%~dp0build\extracted\firmware\shared-fonts"
if not exist "%~dp0build\engage-v200-cont\engage.exe" (
  echo Build the experimental executable first. See docs\engage-port.md.
  exit /b 1
)
"%~dp0build\engage-v200-cont\engage.exe" %*
