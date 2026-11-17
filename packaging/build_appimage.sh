#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="${APPIMAGE_ARCH:-$(uname -m)}"
DIST_NAME="${RTS_DIST_NAME:-RealtimeSubtitle}"
DIST_DIR="${ROOT_DIR}/dist/${DIST_NAME}"
APPDIR="${ROOT_DIR}/build/AppDir"
TOOLS_DIR="${ROOT_DIR}/build/tools"
PYTHON_BIN="${PYTHON_BIN:-python}"
OUTPUT_PATH="${ROOT_DIR}/dist/RealtimeSubtitle-linux-${ARCH}.AppImage"

if [[ ! -x "${DIST_DIR}/${DIST_NAME}" ]]; then
    echo "ERROR: Expected PyInstaller onedir binary at ${DIST_DIR}/${DIST_NAME}" >&2
    echo "Run: RTS_ONEDIR=1 ${PYTHON_BIN} packaging/build_exe.py" >&2
    exit 1
fi

rm -rf "${APPDIR}"
mkdir -p \
    "${APPDIR}/usr/lib/realtime-subtitle" \
    "${APPDIR}/usr/bin" \
    "${APPDIR}/usr/share/applications" \
    "${APPDIR}/usr/share/icons/hicolor/256x256/apps" \
    "${TOOLS_DIR}"

cp -a "${DIST_DIR}/." "${APPDIR}/usr/lib/realtime-subtitle/"
ln -s "../lib/realtime-subtitle/${DIST_NAME}" "${APPDIR}/usr/bin/realtime-subtitle"

# Convert bundled app.ico to a 256x256 PNG icon required by AppImage/desktop specs.
QT_QPA_PLATFORM=offscreen "${PYTHON_BIN}" - "${ROOT_DIR}/static/app.ico" "${APPDIR}/realtime-subtitle.png" <<'PY'
import sys
from PySide6.QtGui import QGuiApplication, QIcon

app = QGuiApplication.instance() or QGuiApplication([])
icon = QIcon(sys.argv[1])
pixmap = icon.pixmap(256, 256)
if pixmap.isNull() or not pixmap.save(sys.argv[2], "PNG"):
    raise SystemExit(f"Failed to convert icon {sys.argv[1]} to {sys.argv[2]}")
PY
cp "${APPDIR}/realtime-subtitle.png" "${APPDIR}/.DirIcon"
cp "${APPDIR}/realtime-subtitle.png" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/realtime-subtitle.png"

cat > "${APPDIR}/RealtimeSubtitle.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Realtime Subtitle
Comment=Real-time streaming speech translator and subtitle overlay
Exec=realtime-subtitle
Icon=realtime-subtitle
Categories=AudioVideo;Utility;
Terminal=false
StartupWMClass=RealtimeSubtitle
EOF
cp "${APPDIR}/RealtimeSubtitle.desktop" "${APPDIR}/usr/share/applications/RealtimeSubtitle.desktop"

cat > "${APPDIR}/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

HERE="$(dirname "$(readlink -f "$0")")"

# Host WebKit2GTK / GTK3 typelibs are loaded dynamically by PyGObject.
for typelib_dir in \
    "/usr/lib/x86_64-linux-gnu/girepository-1.0" \
    "/usr/lib/aarch64-linux-gnu/girepository-1.0" \
    "/usr/lib64/girepository-1.0" \
    "/usr/lib/girepository-1.0"; do
    if [[ -d "${typelib_dir}" ]]; then
        case ":${GI_TYPELIB_PATH:-}:" in
            *":${typelib_dir}:"*) ;;
            *) GI_TYPELIB_PATH="${GI_TYPELIB_PATH:+${GI_TYPELIB_PATH}:}${typelib_dir}" ;;
        esac
    fi
done
export GI_TYPELIB_PATH

exec "${HERE}/usr/lib/realtime-subtitle/RealtimeSubtitle" "$@"
EOF
chmod +x "${APPDIR}/AppRun"

APPIMAGETOOL="${APPIMAGETOOL:-}"
if [[ -z "${APPIMAGETOOL}" ]]; then
    if command -v appimagetool >/dev/null 2>&1; then
        APPIMAGETOOL="$(command -v appimagetool)"
    else
        APPIMAGETOOL="${TOOLS_DIR}/appimagetool-${ARCH}.AppImage"
        if [[ ! -x "${APPIMAGETOOL}" ]]; then
            curl -fsSL \
                "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage" \
                -o "${APPIMAGETOOL}"
            chmod +x "${APPIMAGETOOL}"
        fi
    fi
fi

rm -f "${OUTPUT_PATH}"
ARCH="${ARCH}" APPIMAGE_EXTRACT_AND_RUN=1 "${APPIMAGETOOL}" "${APPDIR}" "${OUTPUT_PATH}"
echo "AppImage built at: ${OUTPUT_PATH}"
