# ReSync integrado ao Premiere Pro 26.5.2 — Beta 0.3

## Objetivo: sincronizar de dentro do Premiere, sem XML manual

Abra a sequência com as câmeras e áudios, clique em **Sincronizar e criar sequência** no painel do ReSync e aguarde. O painel exporta temporariamente o XML, inicia o motor Python do ReSync.app no Mac, aguarda o resultado e importa o XML sincronizado automaticamente como **uma NOVA sequência dentro do projeto aberto**. A timeline original não é modificada.

O XML continua existindo como transporte interno; o usuário não precisa abrir o app nem exportar/importar manualmente.

### Fluxo interno

1. UXP `ProjectConverter.exportAsFinalCutProXML` exporta a sequência ativa para uma pasta autorizada pelo usuário.
2. UXP cria um arquivo `resync_<ID>.job.json` com o protocolo v1 e chama `shell.openExternal('resync://sync?job=...')`.
3. O ReSync.app instalado no macOS registra o esquema `resync://`. O bridge executa `resync-server --resync-job /pasta/job.json` como processo separado, preferencialmente sem abrir a janela.
4. O motor lê o XML, analisa o áudio original localmente e publica `resync_<ID>.status.json` de forma atômica. Uma falha não gera XML importável.
5. Após conclusão, o motor grava `resync_<ID>.synced.xml` com nome de sequência exclusivo.
6. UXP chama `project.importFiles([xmlPath], true, null, false)`, verifica a nova sequência pelo nome e então `project.openSequence(nova)`. Os clipes da timeline original permanecem onde estavam.

**Nota importante:** O passo 6 se baseia nas APIs oficiais e em exemplos públicos de importação de XML, mas **não foi validado ainda no Premiere 26.5.2 real**. Caso o Premiere recuse o XML, o arquivo gerado fica na pasta e o painel mostra um erro; não há importação falsa.

### Requisitos

- macOS Apple Silicon com **ReSync.app que inclua o bridge `resync://`**. A versão pública 0.6.6 ainda não inclui esta integração. Instale a compilação **ReSync 0.6.7 Beta** deste PR na pasta **Aplicativos**, substituindo a antiga 0.6.6. Não confundir com a versão do painel.
- Premiere Pro 26.5.2.
- UXP Developer Tool com modo desenvolvedor.
- Primeira execução: selecionar uma pasta de trabalho (pode ficar num SSD). O token de acesso é lembrado para as próximas execuções.
- Confirmação do macOS ao permitir ao plugin iniciar o motor local através do esquema `resync://`. Pode ser lembrada pelo sistema.

### Instalação de desenvolvimento

1. Instale/extraia uma compilação **do ReSync.app com o bridge** e mova-o para a pasta Aplicativos.
2. Baixe o código do repositório com esta branch e extraia-o.
3. Abra o **UXP Developer Tool** → Add Plugin → selecione `premiere-uxp/manifest.json` → Load & Watch.
4. Se você já carregou uma versão anterior do plugin, use **Unload** e remova o plugin antigo do UXP Developer Tool antes de adicionar o novo `manifest.json` da Beta 0.3. Depois abra o painel em Janela → UXP Plugins → ReSync.
5. Abra uma sequência que contenha as câmeras/áudios que deseja sincronizar.
6. Clique em **Sincronizar e criar sequência**.
7. Escolha uma pasta de trabalho na primeira vez. O plugin pode pedir autorização para abrir o ReSync.
8. Aguarde e confirme que surgiu uma **segunda sequência**, com sufixo ReSync, sem afetar a original.

### Se o macOS pedir para abrir um arquivo JSON, ou o painel parecer travado

Isso geralmente significa que o esquema `resync://` não foi associado à versão beta do ReSync.app. Não selecione arquivos JSON no diálogo: feche esse diálogo.

1. Clique em **Interromper espera** no painel, ou descarregue o plugin pelo UXP Developer Tool.
2. No Finder, abra **Aplicativos → ReSync → Obter Informações**. Confirme que a versão é **0.6.7** (e não 0.6.6). Se for 0.6.6, instale o ReSync.app Beta a partir do artefato do macOS CI deste PR.
3. Inicie o ReSync.app Beta uma vez pelo Finder e feche-o. Se o macOS bloquear a abertura, libere em Privacidade e Segurança após conferir a origem.
4. Reinicie o Premiere, carregue novamente o painel Beta 0.3 e tente com uma sequência de teste pequena.
5. Se não houver status do motor em aproximadamente 35 segundos, o painel mostrará erro e liberará o botão automaticamente (em vez de esperar 90 minutos).

A versão Beta não é notarizada pela Apple. O uso do esquema `resync://` e a importação da sequência final ainda precisam de confirmação no Premiere 26.5.2 real.

### Limitações conhecidas do beta

- **Ainda não foi testado em uma instância real do Premiere**; testes Node simulam a API.
- ReSync.app e plugin precisam estar instalados no mesmo Mac; rodar só o plugin sem o bridge fará a operação expirar.
- O fluxo inicial usa XML do Premiere, que pode não preservar todos os efeitos avançados/transições. Por isso, é recomendado partir de uma sequência de brutos, antes de edição criativa.
- WAVs independentes não estão validados para todos os materiais.
- `project.importFiles` pode importar também mídias duplicadas no painel Projeto.
- Se o Premiere não conseguir identificar a nova sequência, o XML produzido não será perdido: estará na pasta escolhida.
- Embora exista o arquivo temporário de XML, **nenhum material é enviado à nuvem**.
- Não há versão Windows desta ponte Swift/macOS ainda.

### Testes sem Premiere

O GitHub Actions executa:
`python3 -m unittest discover -s source/tests -p 'test_*.py' -q`

`node --test premiere-uxp/tests/*.test.cjs`

Os testes incluem preservação do XML original em sucesso/erro, estado do worker e simulação de exportação→lançamento→importação de uma nova sequência.

### Documentação usada

- https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectconverter
- https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project
- https://developer.adobe.com/premiere-pro/uxp/resources/recipes/external-process/
- https://developer.adobe.com/premiere-pro/uxp/resources/recipes/filesystem-operations/
