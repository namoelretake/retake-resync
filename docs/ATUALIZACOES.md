# ReSync — atualizações pelo próprio aplicativo

## Distribuição pública

O código e os instaladores são distribuídos pelo **mesmo repositório público**:
https://github.com/namoelretake/retake-resync

O botão **Buscar atualizações** usa o Sparkle 2.10.0, e o appcast fica sempre no asset `appcast.xml` do último GitHub Release:
https://github.com/namoelretake/retake-resync/releases/latest/download/appcast.xml

**Atenção:** o GitHub não muda a visibilidade do repositório sozinho. Em Settings → General → Danger Zone → Change repository visibility, o proprietário precisa escolher Public e confirmar. Confira antes de publicar se não há material confidencial no histórico do Git.

## Situação 0.6.3

- Botão implementado em Preferências.
- Sparkle integrado na versão para Mac; 13 testes do motor passaram no GitHub.
- Sem chave EdDSA **pública** no bundle, mostra "Atualizações em preparação".
- A 0.6.3 configurada será a **primeira instalação manual**. Somente versões posteriores, publicadas como Releases corretamente assinados, poderão ser instaladas pelo próprio aplicativo.

## Assistente simples para macOS

Para configurar as chaves sem digitar comandos de criptografia:

1. Baixe `tools/Configurar_Chaves_ReSync.command` deste repositório.
2. Abra Terminal e execute `bash ~/Downloads/Configurar_Chaves_ReSync.command` (se baixou em Downloads).
3. O assistente valida o Sparkle 2.10.0 por SHA-256, gera uma chave EdDSA exclusiva do ReSync no Chaves do macOS, copia primeiro a chave pública e depois a privada, abrindo a página de segredos do GitHub em cada etapa.
4. Cadastre os segredos com os nomes exatos indicados pelo assistente. Não envie os valores ao ChatGPT.

A chave privada é exportada apenas para pasta temporária com acesso restrito e o assistente apaga o arquivo e limpa a área de transferência ao encerrar. Faça backup seguro da chave que permanece no Chaves do macOS.

## Configuração única das chaves

1. No seu Mac, baixe o Sparkle 2.10.0 da página oficial: https://github.com/sparkle-project/Sparkle/releases/tag/2.10.0.
2. Execute `bin/generate_keys` do Sparkle. Ele gera um par EdDSA e guarda a chave privada no Chaves do macOS. Copie a chave pública impressa.
3. Exporte a chave **privada para um arquivo local seguro** usando `generate_keys -x /caminho/privada.key` (consulte `generate_keys --help`). Faça backup criptografado e não envie a chave privada em mensagens ou commits.
4. Abra Settings → Secrets and variables → Actions → New repository secret no GitHub. Salve:
   - `SPARKLE_PUBLIC_ED_KEY`: valor da chave pública (base64);
   - `SPARKLE_PRIVATE_ED_KEY`: conteúdo do arquivo exportado da chave privada (base64).
5. Exclua a cópia temporária em texto plano depois do backup e do cadastro do segredo, preservando a versão guardada no Chaves do Mac.

A compilação usa a chave **pública** no `Info.plist`; a rotina de release lê a chave **privada somente no runner do GitHub**, grava em arquivo temporário com permissão restrita, assina o ZIP do aplicativo e apaga o arquivo.

**Nunca** mande o conteúdo da chave privada ao ChatGPT.

## Publicação das versões

Após configurar os dois segredos e tornar o repo público:

1. Atualize `CFBundleShortVersionString` e `CFBundleVersion` em `build/Info.plist` (números de build crescentes), e o número visível em `source/index.html`.
2. Abra Actions → **ReSync — publicar atualização macOS** → Run workflow (branch main).
3. O workflow executa os 13 testes, compila em macOS ARM64, gera um ZIP com links simbólicos preservados (`ditto`), assina com Sparkle EdDSA, cria `appcast.xml` e publica os três assets como GitHub Release.
4. Instale manualmente a primeira versão com chave pública válida (0.6.3). Nas próximas, abra ReSync → Preferências → Buscar atualizações.

## Avisos

- Não faça release enquanto os dois segredos não estiverem cadastrados: o workflow falhará antes de publicar.
- `macos-build.yml` cria apenas artefatos de teste não assinados. O pipeline de release é separado e exige chaves.
- Para distribuição profissional a outras pessoas, recomenda-se assinatura Apple Developer ID e notarização. Essa etapa **não está implementada**, portanto o macOS pode mostrar alertas de segurança.
- O FFmpeg empacotado pode incorporar componentes GPL; antes de distribuição ampla, verifique os avisos, licenças e obrigações de redistribuição.
- Teste pelo menos uma atualização real entre versões consecutivas no seu Mac antes de considerar o sistema pronto.
