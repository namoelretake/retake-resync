# ReSync para Adobe Premiere Pro 26.5.2 — protótipo UXP

Primeiro painel interno do Premiere usando APIs oficiais UXP.
**Não modifica a timeline. Não realiza sincronização dentro do Premiere ainda.**

## Instalação de desenvolvimento

1. Baixe o código do repositório (Code → Download ZIP), extraia-o.
2. Instale **UXP Developer Tools** pela Adobe Creative Cloud.
3. Abra o Premiere Pro 26.5.2 com um projeto e uma sequência aberta.
4. Abra o **UXP Developer Tool** e habilite o Developer Mode.
5. Clique em **Add Plugin** e selecione `premiere-uxp/manifest.json`.
6. Clique em **Load & Watch** ou **Load**.
7. No Premiere, abra **Janela → UXP Plugins → ReSync**.

## O que já funciona no protótipo

- Reconhecer projeto e sequência ativos, atualizar as informações no painel.
- Contar pistas de vídeo e de áudio pela API do Premiere.
- Exportar **Final Cut Pro 7 XML** usando `ProjectConverter.exportAsFinalCutProXML` após você selecionar a pasta, sem alterar a sequência original.
- Mostrar o local do arquivo para você importá-lo no **ReSync.app**.

## Próximas etapas

Integrar o motor Python sem depender de uma conexão HTTP insegura (UXP restringe `http://` no macOS). Em seguida, importar o resultado numa **nova sequência**, com testes para evitar conflitos de clipes e perdas de efeitos, preservando a original.

O projeto usa o `premiere-uxp` como fonte exclusiva do painel. O ReSync.app continua sendo distribuído separadamente e não é alterado por este protótipo.

## Desenvolvimento

Execute `node --test premiere-uxp/tests/*.test.cjs` em um ambiente Node 20+ para testar o código com Premiere simulado. A aprovação desses testes não substitui o teste dentro do Premiere 26.5.2.

Documentação Adobe:
- https://developer.adobe.com/premiere-pro/uxp/ppro-reference/
- https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectconverter
- https://developer.adobe.com/premiere-pro/uxp/plugins/
