#!/bin/bash
# ReSync by Retake — one-time Sparkle EdDSA signing-key setup.
# No credentials are sent to ChatGPT or committed to git.
set -Eeuo pipefail
umask 077

SPARKLE_VERSION="2.10.0"
SPARKLE_SHA256="c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c"
GH_SECRETS_URL="https://github.com/namoelretake/retake-resync/settings/secrets/actions/new"
ACCOUNT="com.retakefilmes.resync"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/resync-sparkle.XXXXXXXX")"
finish() {
  printf '' | pbcopy || true
  rm -rf "$TEMP_DIR"
}
trap finish EXIT

message() { printf "\n%s\n" "$1"; }
abort() {
  message "Não foi possível concluir: $1"
  read -r -p "Pressione Enter para fechar..." _ || true
  exit 1
}
[[ "$(uname -s)" == "Darwin" ]] || abort "Este assistente só funciona no macOS."
command -v pbcopy >/dev/null || abort "Área de transferência do macOS indisponível."

message "══════════════════════════════════════════════════════"
message " ReSync — configuração única das atualizações seguras "
message "══════════════════════════════════════════════════════"
message "Nenhuma chave será mostrada ao ChatGPT ou enviada ao repositório."
message "Você fará duas colagens nas configurações privadas do GitHub."
read -r -p "Pressione Enter para começar..." _

message "1/3 — Baixando as ferramentas oficiais do Sparkle…"
ARCHIVE="$TEMP_DIR/Sparkle.tar.xz"
curl --fail --location --silent --show-error --retry 3 \
  "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz" \
  --output "$ARCHIVE" || abort "Não foi possível obter o Sparkle."
echo "$SPARKLE_SHA256  $ARCHIVE" | shasum -a 256 -c - || abort "Verificação da assinatura SHA-256 falhou."
mkdir -p "$TEMP_DIR/sparkle"
tar -xJf "$ARCHIVE" -C "$TEMP_DIR/sparkle" || abort "Não foi possível extrair o Sparkle."
KEY_TOOL="$(find "$TEMP_DIR/sparkle" -maxdepth 5 -type f -path '*/bin/generate_keys' -print -quit)"
[[ -n "$KEY_TOOL" ]] || abort "Ferramenta generate_keys não encontrada."
[[ -x "$KEY_TOOL" ]] || chmod +x "$KEY_TOOL"

# Reuse a dedicated Keychain account to avoid creating unrelated keys.
if ! "$KEY_TOOL" --account "$ACCOUNT" -p >/dev/null 2>&1; then
  message "Gerando uma chave exclusiva do ReSync no Chaves do macOS…"
  "$KEY_TOOL" --account "$ACCOUNT" || abort "Falha ao gerar a chave no Chaves."
fi
PUBLIC_KEY="$("$KEY_TOOL" --account "$ACCOUNT" -p)" || abort "Chave pública não encontrada."
[[ -n "$PUBLIC_KEY" ]] || abort "Chave pública vazia."

message "2/3 — Cadastre a chave PÚBLICA no GitHub"
printf '%s' "$PUBLIC_KEY" | pbcopy
message "O valor público foi copiado para sua área de transferência."
message "No site que vai abrir, preencha:"
message "  Name: SPARKLE_PUBLIC_ED_KEY"
message "  Secret: cole com Command + V"
message "Depois clique em Add secret."
open "$GH_SECRETS_URL"
read -r -p "Após salvar o primeiro segredo no GitHub, pressione Enter..." _

message "3/3 — Cadastre a chave PRIVADA no GitHub"
PRIVATE_KEY_FILE="$TEMP_DIR/private.key"
"$KEY_TOOL" --account "$ACCOUNT" -x "$PRIVATE_KEY_FILE" || abort "Não foi possível exportar a chave privada."
chmod 600 "$PRIVATE_KEY_FILE"
pbcopy < "$PRIVATE_KEY_FILE"
message "A chave privada foi copiada temporariamente para a área de transferência."
message "No site que vai abrir, preencha:"
message "  Name: SPARKLE_PRIVATE_ED_KEY"
message "  Secret: cole com Command + V"
message "Depois clique em Add secret."
message "Nunca envie essa chave para o ChatGPT, WhatsApp ou arquivos públicos."
open "$GH_SECRETS_URL"
read -r -p "Após salvar o segundo segredo no GitHub, pressione Enter..." _

message "O assistente terminou. A área de transferência será limpa."
message "A chave continua no Chaves do seu Mac e nos segredos protegidos do GitHub."
message "Próximo passo: validar os segredos executando o workflow de publicação do ReSync."
read -r -p "Pressione Enter para fechar..." _
