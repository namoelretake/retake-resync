# ReSync — atualizações automáticas no macOS

## Arquitetura

- **Código privado** no `namoelretake/retake-resync`.
- **Canal público só para instaladores**: o repositório `namoelretake/resync-updates`, ainda a ser criado e autorizado pelo proprietário.
- **Atualizador nativo**: Sparkle 2.10.0 integrado ao `ReSync.app`. O Sparkle substitui o aplicativo e oferece reinicialização segura.
- **Feed**: `https://raw.githubusercontent.com/namoelretake/resync-updates/main/appcast.xml`, apontando para arquivos de releases hospedados no repositório público de distribuição.
- **Assinatura**: cada arquivo de atualização precisa de assinatura EdDSA. O aplicativo contém somente a chave **pública**. A chave **privada nunca deve ser enviada ao repositório, aplicativo ou conversa**.

## Situação atual (0.6.3)

O botão **Buscar atualizações** está integrado às Preferências e envia a ação para o atualizador macOS. O framework Sparkle já faz parte da compilação.

**Ainda não haverá instalação de atualização** enquanto faltarem:
1. O repositório público `resync-updates` com feed e releases assinados.
2. A chave pública EdDSA fornecida na compilação (`SPARKLE_PUBLIC_ED_KEY`).
3. Uma compilação inicial do ReSync 0.6.3 com essa chave pública, instalada manualmente **uma última vez**.

Se não houver chave pública na compilação, o programa informa "Atualizações em preparação" em vez de simular sucesso.

## Preparação de assinatura — no Mac do proprietário

1. Baixe a distribuição oficial do Sparkle em https://github.com/sparkle-project/Sparkle/releases/tag/2.10.0 e confira SHA-256 `c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c`.
2. Execute `bin/generate_keys` (conforme a documentação Sparkle). Isso cria o par EdDSA e guarda a chave privada no Chaves do macOS.
3. Guarde a chave privada/backup em local seguro; não cole a chave privada no GitHub nem no ChatGPT.
4. Copie **somente a chave pública**, gerada pelo Sparkle, para o segredo `SPARKLE_PUBLIC_ED_KEY` do repositório de código, ou passe como variável de ambiente ao compilar localmente.
5. Compile novamente, instale a versão inicial e teste o botão.

## Como publicar atualizações depois

1. Incrementar `CFBundleVersion` e `CFBundleShortVersionString` em `build/Info.plist`.
2. Rodar testes e compilar a versão para Apple Silicon.
3. Compactar `ReSync.app` preservando atributos, permissões e links simbólicos (ex.: `ditto -c -k --sequesterRsrc --keepParent ReSync.app ReSync-0.6.4.zip`).
4. Usar `bin/sign_update` ou `bin/generate_appcast` do Sparkle para assinar o ZIP com a chave EdDSA privada.
5. Publicar o ZIP como GitHub Release em `resync-updates` e o `appcast.xml` assinado apontando para a URL real do arquivo.
6. O botão **Buscar atualizações** abre a experiência nativa do Sparkle: comparação, download, verificação, atualização e reinicialização.

**Importante:** não adianta fazer push de código novo. Sem release, assinatura válida e appcast atualizado o ReSync corretamente indicará que não há atualização.

## Distribuição e segurança

- Não usar um token pessoal do GitHub no código-fonte ou dentro do .app.
- Nunca baixar e executar binários de URLs arbitrárias sem autenticação da assinatura.
- Sparkle gerencia a assinatura EdDSA do pacote; para distribuição a terceiros recomenda-se também assinatura Developer ID e notarização pela Apple.
- O código-fonte pode continuar privado, mas o canal público de instaladores será baixável por outras pessoas que tiverem a URL. Se essa exposição for indesejada, precisaremos construir um canal privado com autenticação do usuário.
- A distribuição de FFmpeg com suporte a componentes GPL exige revisar as respectivas licenças antes de tornar os binários públicos.
- A primeira instalação da versão com atualizador ainda é manual. Versões futuras serão instaladas pelo aplicativo.
