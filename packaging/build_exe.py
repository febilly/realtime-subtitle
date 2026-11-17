"""
PyInstaller build script for realtime-subtitle
This script creates a single executable file
"""

import PyInstaller.__main__
import os
import sys

# Get the current directory
packaging_dir = os.path.dirname(os.path.abspath(__file__))
# dist/ and build/ stay at the repository root, as before the spec moved here.
current_dir = os.path.dirname(packaging_dir)

spec_file = os.path.join(packaging_dir, 'RealtimeSubtitle.spec')

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

dist_name = os.environ.get('RTS_DIST_NAME') or 'RealtimeSubtitle'
if os.environ.get('RTS_ONEDIR') == '1':
    output_path = os.path.join(current_dir, 'dist', dist_name)
elif sys.platform == 'win32':
    output_path = os.path.join(current_dir, 'dist', f'{dist_name}.exe')
else:
    output_path = os.path.join(current_dir, 'dist', dist_name)

print("\n" + "="*60)
print("Build completed!")
print(f"Executable location: {output_path}")
print("="*60)
