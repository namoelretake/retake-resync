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
cp "$BASE/build/Info.plist" "$APP/Contents/Info.plist"

echo "Incluindo FFmpeg para não depender do Homebrew…"
FFMPEG_BIN=$("$VENV/bin/python" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')
[[ -f "$FFMPEG_BIN" ]] || die "O pacote FFmpeg não forneceu um executável."
cp "$FFMPEG_BIN" "$APP/Contents/Resources/bin/ffmpeg"
chmod 755 "$APP/Contents/Resources/bin/ffmpeg"

# Generate PNG and .icns from the selected R. artwork on every build,
# preventing the Finder/Dock icon from retaining the previous app icon.
echo "Preparando ícone oficial do ReSync (R. branco, fundo preto)…"
ICON_SOURCE="$BASE/assets/ReSyncIconSource.webp"
[[ -s "$ICON_SOURCE" ]] || die "A arte original do ícone não foi encontrada."
"$FFMPEG_BIN" -hide_banner -loglevel error -y -i "$ICON_SOURCE" \
  -frames:v 1 -vf "scale=1024:1024:flags=lanczos" -pix_fmt rgba \
  "$APP/Contents/Resources/ReSyncIcon.png"
[[ -s "$APP/Contents/Resources/ReSyncIcon.png" ]] || die "Falha ao converter o ícone em PNG."
ICONSET="$BASE/.build-temp/ReSync.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  double=$((size * 2))
  sips -s format png -z "$size" "$size" "$APP/Contents/Resources/ReSyncIcon.png" \
    --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -s format png -z "$double" "$double" "$APP/Contents/Resources/ReSyncIcon.png" \
    --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/ReSyncIcon.icns"
[[ -s "$APP/Contents/Resources/ReSyncIcon.icns" ]] || die "Falha ao gerar o ícone nativo do macOS."


echo "Obtendo Sparkle para atualizações seguras…"
SPARKLE_VERSION="2.10.0"
SPARKLE_SHA256="c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c"
SPARKLE_DIR="$BASE/.build-temp/Sparkle"
SPARKLE_ARCHIVE="$BASE/.build-temp/Sparkle-${SPARKLE_VERSION}.tar.xz"
mkdir -p "$SPARKLE_DIR" "$APP/Contents/Frameworks"
curl --fail --location --silent --show-error --retry 3 \
  "https://github.com/sparkle-project/Sparkle/releases/download/${SPARKLE_VERSION}/Sparkle-${SPARKLE_VERSION}.tar.xz" \
  --output "$SPARKLE_ARCHIVE"
echo "${SPARKLE_SHA256}  ${SPARKLE_ARCHIVE}" | shasum -a 256 -c -
tar -xJf "$SPARKLE_ARCHIVE" -C "$SPARKLE_DIR"
FRAMEWORK_SRC="$(find "$SPARKLE_DIR" -maxdepth 3 -name Sparkle.framework -type d -print -quit)"
[[ -n "$FRAMEWORK_SRC" ]] || die "Sparkle.framework não encontrado na distribuição."
ditto "$FRAMEWORK_SRC" "$APP/Contents/Frameworks/Sparkle.framework"

echo "Compilando janela nativa em WebKit + Sparkle…"
DEPLOYMENT_TARGET=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$APP/Contents/Info.plist")
xcrun swiftc -O -target "$(uname -m)-apple-macos$DEPLOYMENT_TARGET" -framework Cocoa -framework WebKit \
  -F "$APP/Contents/Frameworks" -framework Sparkle \
  -Xlinker -rpath -Xlinker "@executable_path/../Frameworks" \
  "$BASE/native/ReSyncNative.swift" -o "$APP/Contents/MacOS/ReSync"
chmod 755 "$APP/Contents/MacOS/ReSync"

# Sparkle requires a public EdDSA key embedded in the app. The private key
# NEVER belongs in the source repository or app bundle.
# Releases stay disabled until a properly signed public distribution exists.
if [[ -n "${SPARKLE_PUBLIC_ED_KEY:-}" ]]; then
  /usr/libexec/PlistBuddy -c "Add :SUPublicEDKey string ${SPARKLE_PUBLIC_ED_KEY}" "$APP/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :SUFeedURL string https://github.com/namoelretake/retake-resync/releases/latest/download/appcast.xml" "$APP/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :SUEnableAutomaticChecks bool true" "$APP/Contents/Info.plist"
  echo "Canal Sparkle configurado. Publicações precisarão da chave EdDSA privada correspondente."
else
  echo "Aviso: SPARKLE_PUBLIC_ED_KEY não informado; atualização automática permanece desativada."
fi


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

