#!/bin/bash
# Build on the user's Mac: true standalone arm64/x86_64 app for the current architecture.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
BASE="$PWD"
OUT="$BASE/saida"
APP="$OUT/ReSync.app"
VENV="$BASE/.build-venv"
mkdir -p "$OUT" "$BASE/.build-temp"

die() { echo; echo "ERRO: $*"; echo; read -r -p "Pressione Enter para fechar..." _; exit 1; }
trap 'echo "A compilação encontrou um erro. Confira as mensagens acima."' ERR
[[ "$(uname -s)" == "Darwin" ]] || die "Este instalador precisa ser compilado em um Mac."
command -v xcrun >/dev/null || die "Instale as Ferramentas de Linha de Comando: xcode-select --install"
xcrun --find swiftc >/dev/null 2>&1 || die "Instale as ferramentas de desenvolvimento da Apple: xcode-select --install"
command -v python3 >/dev/null || die "É necessário Python 3.10 ou superior para compilar."

echo "=== ReSync para macOS — compilação local ==="
echo "Arquitetura: $(uname -m)"
python3 -m venv "$VENV" || die "Falha ao preparar ambiente Python."
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install 'numpy>=1.24,<3' 'pyinstaller>=6.5,<7' 'imageio-ffmpeg>=0.5,<1'
echo "Compilando motor Python…"
"$VENV/bin/pyinstaller" --noconfirm --clean --onedir --name resync-server \
  --distpath "$BASE/.build-dist" --workpath "$BASE/.build-temp" \
  --specpath "$BASE/.build-temp" --add-data "$BASE/source/index.html:." \
  --paths "$BASE/source" "$BASE/source/app.py"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/backend" "$APP/Contents/Resources/bin"
cp -R "$BASE/.build-dist/resync-server" "$APP/Contents/Resources/backend/"
cp "$BASE/assets/ReSyncIcon.png" "$APP/Contents/Resources/ReSyncIcon.png"
cp "$BASE/build/Info.plist" "$APP/Contents/Info.plist"

echo "Incluindo FFmpeg para não depender do Homebrew…"
FFMPEG_BIN=$("$VENV/bin/python" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')
[[ -f "$FFMPEG_BIN" ]] || die "O pacote FFmpeg não forneceu um executável."
cp "$FFMPEG_BIN" "$APP/Contents/Resources/bin/ffmpeg"
chmod 755 "$APP/Contents/Resources/bin/ffmpeg"

echo "Compilando janela nativa em WebKit…"
xcrun swiftc -O -framework Cocoa -framework WebKit "$BASE/native/ReSyncNative.swift" \
  -o "$APP/Contents/MacOS/ReSync"
chmod 755 "$APP/Contents/MacOS/ReSync"

cp "$BASE/assets/ReSyncIcon.icns" "$APP/Contents/Resources/ReSyncIcon.icns"

# Ad-hoc signature is suitable for a locally built app; public distribution requires Developer ID notarization.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "Aviso: assinatura local não pôde ser aplicada."

echo "Criando imagem DMG…"
rm -f "$OUT/ReSync-macOS.dmg"
hdiutil create -volname 'ReSync by Retake' -srcfolder "$APP" -ov -format UDZO "$OUT/ReSync-macOS.dmg" >/dev/null || true

echo
printf 'COMPILAÇÃO CONCLUÍDA!\nAplicativo: %s\n' "$APP"
printf 'DMG: %s\n' "$OUT/ReSync-macOS.dmg"
echo "Abra ReSync.app (ou arraste para Aplicativos)."
if [[ "${CI:-}" != "true" ]]; then
  open "$OUT"
  if [[ -t 0 ]]; then read -r -p "Pressione Enter para fechar..." _; fi
fi
