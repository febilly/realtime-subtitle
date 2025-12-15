"""
PyInstaller build script for realtime-subtitle
This script creates a single executable file
"""

import PyInstaller.__main__
import os

# Get the current directory
current_dir = os.path.dirname(os.path.abspath(__file__))

# Build command
PyInstaller.__main__.run([
    'server.py',                    # Main script
    '--onefile',                     # Create single executable
    '--console',                     # Show console window (for server logs)
    '--name=RealtimeSubtitle',      # Name of the executable
    '--icon=NONE',                   # No icon (you can add one later)
    '--add-data=static;static',     # Include static directory
    '--hidden-import=websockets.sync.client',
    '--hidden-import=aiohttp',
    '--hidden-import=soundcard',
    '--hidden-import=numpy',
    '--hidden-import=dotenv',
    '--hidden-import=locale',
    '--hidden-import=pykakasi',
    '--hidden-import=pythonosc',
    '--collect-all=soundcard',      # Include all soundcard files
    '--collect-all=aiohttp',        # Include all aiohttp files
    '--collect-all=websockets',     # Include all websockets files
    '--collect-all=pykakasi',       # Include all pykakasi files (Japanese furigana)
    '--collect-all=pythonosc',      # Include all pythonosc files (OSC communication)
    f'--distpath={os.path.join(current_dir, "dist")}',
    f'--workpath={os.path.join(current_dir, "build")}',
    f'--specpath={current_dir}',
])

print("\n" + "="*60)
print("Build completed!")
print(f"Executable location: {os.path.join(current_dir, 'dist', 'RealtimeSubtitle.exe')}")
print("="*60)
