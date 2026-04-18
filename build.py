"""Build script: installs deps and runs PyInstaller using the same Python."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist" / "SignWell Invoicer"

DEPS = [
    "pyinstaller",
    "customtkinter",
    "pyyaml",
    "httpx",
    "pydantic",
    "pydantic-settings",
    "typer",
    "rich",
    "email-validator",
]

HIDDEN = [
    "invoicer.config",
    "invoicer.models",
    "invoicer.sender",
    "invoicer.signwell",
    "invoicer.tracking",
    "invoicer.gui",
    "yaml",
    "email_validator",
    "pydantic_settings",
]


def run(cmd):
    result = subprocess.run(cmd, check=True)
    return result


def main():
    print("=== Installing dependencies ===")
    run([sys.executable, "-m", "pip", "install", "--quiet", *DEPS])

    print("\n=== Building ===")
    args = [
        sys.executable, "-m", "PyInstaller",
        "-y", "--onedir", "--windowed",
        "--name", "SignWell Invoicer",
        "--collect-all", "customtkinter",
    ]
    for h in HIDDEN:
        args += ["--hidden-import", h]
    args.append("run_gui.py")

    run(args)

    print(f"\n=== Done ===")
    print(f"Folder: {DIST}")
    print(f"Exe:    {DIST / 'SignWell Invoicer.exe'}")
    print("\nCopy .env and clients.yaml into the folder before distributing.")


if __name__ == "__main__":
    main()
