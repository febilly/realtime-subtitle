"""
PyInstaller build script for realtime-subtitle
This script creates a single executable file
"""

import PyInstaller.__main__
import os

# Get the current directory
current_dir = os.path.dirname(os.path.abspath(__file__))

spec_file = os.path.join(current_dir, 'RealtimeSubtitle.spec')

if not os.path.exists(spec_file):
    raise FileNotFoundError(f"Spec file not found: {spec_file}")

# Build command (all dependency-specific settings live in the .spec file)
PyInstaller.__main__.run([
    spec_file,
    '--noconfirm',
    '--clean',
    f'--distpath={os.path.join(current_dir, "dist")}',
    f'--workpath={os.path.join(current_dir, "build")}',
])

print("\n" + "="*60)
print("Build completed!")
print(f"Executable location: {os.path.join(current_dir, 'dist', 'RealtimeSubtitle.exe')}")
print("="*60)
