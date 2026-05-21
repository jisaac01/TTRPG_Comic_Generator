# 1. VirtualBox + Windows 11 Setup (on your Mac)

Before creating the VM, do this:

- Download & install VirtualBox:
   - Go to https://www.virtualbox.org/wiki/Downloads
   - Download the latest macOS build for your machine:
      - Apple Silicon/ARM: macOS ARM `.dmg`
      - Intel: macOS Intel `.dmg`
   - Install VirtualBox.
   - Install the VirtualBox Extension Pack from the same page (better USB/TPM support).

- Download Windows 11 ISO:
   - Go to Microsoft's official page: https://www.microsoft.com/en-us/software-download/windows11
   - Under "Download Windows 11 Disk Image (ISO)", select:
      - Windows 11 (multi-edition ISO) (ARM edition)
      - language
   - Download the ISO (about 8 GB).

Create VM with these settings:

- OS Type: Microsoft Windows -> Version: Windows 11 (64-bit)
- Edition: Windows 11 Pro
- Memory: >= 8 GB
- CPU: >= 4 cores
- Enable EFI, TPM 2.0, Secure Boot
- Video: 128 MB + 3D Acceleration
- Hard disk: >= 64 GB (dynamic)

Install Windows 11, let it fully update (run Windows Update multiple times until nothing left).

Install Guest Additions:

- In running VM: Devices -> Insert Guest Additions CD Image
- Run VBoxWindowsAdditions.exe (or arm64 version) and reboot.

Set up a Shared Folder (point to your Mac project folder) with Auto-mount.

## 2. Inside Windows 11 VM (Do These First)

Open PowerShell as normal user.

```powershell
# 1. Full Windows Update (critical for SSL certs)
# Settings -> Windows Update -> Check for updates (repeat until clean) -> Reboot

# 2. Enable Developer Mode (required for Flutter symlinks)
start ms-settings:developers
# -> Turn ON "Developer Mode" -> Yes

# 3. Install Python + basic tools
# (Install Python 3.12+ with "Add to PATH")

# 4. Install Visual Studio Build Tools / Visual Studio 2022 with
#    "Desktop development with C++" workload

# 5. Install VC++ runtime (required by Playwright on Windows)
winget install Microsoft.VCRedist.2015+.x64
```

## 3. Prepare Your Flet Project (Critical Step)

```powershell
# Create a LOCAL folder on C: drive (NEVER build directly on \\VBoxSvr)
mkdir C:\Users\vboxuser\TTRPG_Comic_Generator_local
cd C:\Users\vboxuser\TTRPG_Comic_Generator_local

# Copy your project from the shared folder
Copy-Item -Recurse "\\VBoxSvr\flet_project\TTRPG_Comic_Generator\*" .

# Delete any old/broken venv/build cache
Remove-Item -Recurse -Force .venv, build, .flet -ErrorAction SilentlyContinue
```

## 4. Create Fresh Environment & Install Dependencies

```powershell
# If execution policy blocks activation
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# Create and activate venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Upgrade pip and install from pyproject.toml
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .

# If 'playwright' command is not found, always use module form
# python -m playwright ...
```

## 5. Bundle Chromium Into the App Build

```powershell
# Deterministic browser bundle folder that app can find at runtime
Remove-Item -Recurse -Force .\src\playwright-browsers -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force .\src\playwright-browsers | Out-Null

$env:PLAYWRIGHT_BROWSERS_PATH = (Resolve-Path .\src\playwright-browsers).Path
python -m playwright install chromium
```

## 6. Test & Build

```powershell
# Clean build output (important if app/dll files were locked by a running exe)
Remove-Item -Recurse -Force .\build\windows -ErrorAction SilentlyContinue

# Build (must run on Windows)
flet build windows --verbose
```

Notes:
- `flet build windows` auto-reads `pyproject.toml`.
- Flet may auto-download Flutter on first build.

## 7. Verify Browser Was Actually Packaged

```powershell
Get-ChildItem -Recurse .\build\windows -Filter chrome-headless-shell.exe
```

Expected: at least one result before launching the EXE.

## Final Output Location

Your executable will be under:

```text
C:\Users\vboxuser\TTRPG_Comic_Generator_local\build\windows\
```

Copy the whole `build\windows` folder back to your Mac via shared folder and zip it for distribution.

## Quick Reference Commands (keep these handy)

```powershell
# Rebuild from scratch
Remove-Item -Recurse -Force build, .flet
flet build windows --verbose --clear-cache

# Verify browser bundle exists in output
Get-ChildItem -Recurse .\build\windows -Filter chrome-headless-shell.exe
```

## Known Failure Modes We Hit

1. `playwright` command not found
   Use `python -m playwright install chromium`.

2. `PermissionError [WinError 5] ... libcrypto-3.dll`
   EXE/build files are locked. Close running EXE, delete `build\windows`, rebuild.

3. App warns browser missing from bundle / scrape fails and points to AppData `ms-playwright`
   Browser was not packaged or not found. Re-run Sections 5, 6, and 7 exactly.

4. Flutter install fails with SSL certificate verification in VM
   Usually stale OS cert/update state. Complete Windows Update cycles and reboot, then rebuild.
   may require 
   ```powershell
   pip install pip-system-certs
   ```