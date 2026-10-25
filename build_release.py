import os
import sys
import stat
import shutil
import subprocess
import time
from pathlib import Path

base_dir = Path(__file__).resolve().parent
python_exe = sys.executable

def find_iscc() -> Path:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    which_iscc = shutil.which("ISCC") or shutil.which("iscc")
    if which_iscc:
        candidates.insert(0, Path(which_iscc))
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return candidates[0]

iscc_exe = find_iscc()

print(f"Base dir: {base_dir}")
print(f"Python: {python_exe}")

def find_signtool():
    which_tool = shutil.which("signtool") or shutil.which("signtool.exe")
    if which_tool:
        return which_tool
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    if kits.is_dir():
        matches = sorted(kits.glob("*/x64/signtool.exe"), reverse=True)
        if matches:
            return str(matches[0])
    return None


def sign_file(path: Path) -> bool:
    """Digitally sign a file when a certificate is configured.

    Configure one of these environment variables to enable signing:
      SIDEMEMO_SIGN_CERT        path to a .pfx/.p12 certificate
      SIDEMEMO_SIGN_PASSWORD    password for the .pfx (optional)
      SIDEMEMO_SIGN_THUMBPRINT  SHA1 thumbprint of a certificate in the store
    """
    sign_tool = find_signtool()
    if not sign_tool:
        print("Signing skipped: signtool.exe not found.")
        return False
    cert_path = os.environ.get("SIDEMEMO_SIGN_CERT")
    thumbprint = os.environ.get("SIDEMEMO_SIGN_THUMBPRINT")
    if not cert_path and not thumbprint:
        print("Signing skipped: no certificate configured (set SIDEMEMO_SIGN_CERT or SIDEMEMO_SIGN_THUMBPRINT).")
        return False
    cmd = [sign_tool, "sign", "/fd", "sha256", "/tr", "http://timestamp.digicert.com", "/td", "sha256"]
    if cert_path:
        cmd += ["/f", cert_path]
        if os.environ.get("SIDEMEMO_SIGN_PASSWORD"):
            cmd += ["/p", os.environ["SIDEMEMO_SIGN_PASSWORD"]]
    else:
        cmd += ["/sha1", thumbprint]
    cmd += [str(path)]
    res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if res.stdout.strip():
        print(res.stdout[-800:])
    if res.stderr.strip():
        print(res.stderr[-800:])
    if res.returncode == 0:
        print(f"Signed: {path}")
        return True
    print(f"Signing failed (exit {res.returncode}): {path}")
    return False


def remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        print(f"Warning: could not remove {path}: {e}")

# 1. Clean build & dist
for d in [base_dir / "dist", base_dir / "build"]:
    if d.exists():
        print(f"Cleaning {d}...")
        for root, dirs, files in os.walk(d):
            for f in files:
                p = os.path.join(root, f)
                try:
                    os.chmod(p, stat.S_IWRITE)
                except Exception:
                    pass
        shutil.rmtree(d, onerror=remove_readonly)

# 2. Sanitize PATH to remove poppler/codex runtime DLL pollution
clean_paths = []
for p in os.environ.get("PATH", "").split(";"):
    p_low = p.lower()
    if any(bad in p_low for bad in ["poppler", "codex-primary-runtime", "codex-runtimes", "dependencies\\native", "\\plugins\\cache"]):
        print(f"Filtered from PATH: {p}")
        continue
    clean_paths.append(p)

sys32 = r"C:\Windows\System32"
if sys32 not in clean_paths:
    clean_paths.insert(0, sys32)

env = os.environ.copy()
env["PATH"] = ";".join(clean_paths)

# 3. Run PyInstaller
print("Running PyInstaller...")
cmd = [
    python_exe, "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", "SideMemo",
    "--icon", "app.ico",
    "--version-file", "version_info.txt",
    "--noupx",
    "sidememo.py"
]
res = subprocess.run(cmd, cwd=str(base_dir), env=env, capture_output=True, encoding="utf-8", errors="replace")
if res.returncode != 0:
    print("PyInstaller ERROR:")
    print(res.stdout[-1500:])
    print(res.stderr[-1500:])
    sys.exit(res.returncode)
print("PyInstaller finished successfully.")

# 4. Fix ICU DLLs in _internal
dist_internal = base_dir / "dist" / "SideMemo" / "_internal"
if not dist_internal.exists():
    print(f"Error: {dist_internal} does not exist!")
    sys.exit(1)

# Remove any incompatible icudt*.dll
for search_dir in [dist_internal, dist_internal / "PySide6"]:
    if search_dir.is_dir():
        for item in search_dir.iterdir():
            if item.name.lower().startswith("icudt") and item.suffix.lower() == ".dll":
                try:
                    os.chmod(item, stat.S_IWRITE)
                    item.unlink()
                    print(f"Removed incompatible ICU DLL: {item.name} from {search_dir}")
                except Exception as e:
                    print(f"Failed to remove {item.name}: {e}")

# Copy Windows System32 ICU DLLs and ensure writeable
for dll_name in ["icu.dll", "icuin.dll", "icuuc.dll"]:
    src = Path(sys32) / dll_name
    for target_dir in [dist_internal, dist_internal / "PySide6"]:
        if target_dir.is_dir() and src.exists():
            dst = target_dir / dll_name
            shutil.copyfile(src, dst)
            os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
            print(f"Copied System32 ICU DLL: {dll_name} -> {dst}")

# 5. Verify QtCore import in the built environment
print("Testing QtCore import...")
verify_py = f"""
import os, sys
os.add_dll_directory(r'{dist_internal}')
os.add_dll_directory(r'{dist_internal / "PySide6"}')
from PySide6 import QtCore, QtWidgets, QtGui
print('QTCORE_IMPORT_SUCCESS: Qt Version', QtCore.__version__)
"""
test_res = subprocess.run([python_exe, "-c", verify_py], capture_output=True, encoding="utf-8", errors="replace")
print("Import test output:", test_res.stdout.strip())
if test_res.stderr.strip():
    print("Import test stderr:", test_res.stderr.strip())
if "QTCORE_IMPORT_SUCCESS" not in test_res.stdout:
    print("ERROR: QtCore import test failed!")
    sys.exit(1)

# 6. Test launching SideMemo.exe directly
if not os.environ.get("GITHUB_ACTIONS") and not os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    exe_path = base_dir / "dist" / "SideMemo" / "SideMemo.exe"
    print(f"Launching {exe_path} to verify startup...")
    test_env = os.environ.copy()
    test_env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.Popen([str(exe_path)], env=test_env)
    time.sleep(1)
    poll_val = proc.poll()
    if poll_val is not None and poll_val != 0:
        print(f"ERROR: SideMemo.exe exited immediately with code {poll_val}")
        sys.exit(1)
    print(f"SideMemo.exe is running properly (PID: {proc.pid}). Terminating test process...")
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()
else:
    print("Running in GitHub Actions CI: skipping GUI launch test.")

# 6b. Sign the built EXE when a certificate is configured
sign_file(base_dir / "dist" / "SideMemo" / "SideMemo.exe")

# 7. Compile with Inno Setup
print(f"Compiling installer with Inno Setup: {iscc_exe}...")
iss_path = base_dir / "SideMemo.iss"
inno_res = subprocess.run([str(iscc_exe), str(iss_path)], cwd=str(base_dir), capture_output=True, encoding="utf-8", errors="replace")
if inno_res.returncode != 0:
    print("Inno Setup ERROR:")
    print(inno_res.stdout[-1500:])
    print(inno_res.stderr[-1500:])
    sys.exit(inno_res.returncode)

installer_exe = base_dir / "installer" / "SideMemo-Setup.exe"
if installer_exe.exists():
    size_mb = installer_exe.stat().st_size / (1024 * 1024)
    mtime = time.ctime(installer_exe.stat().st_mtime)
    print(f"SUCCESS: Installer generated at {installer_exe}")
    print(f"Size: {size_mb:.2f} MB, Last modified: {mtime}")
else:
    print("ERROR: Installer file not found after build!")
    sys.exit(1)

# 8. Sign the installer when a certificate is configured
sign_file(base_dir / "installer" / "SideMemo-Setup.exe")
