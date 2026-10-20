# ReSync integrado ao Premiere Pro 26.5.2 — Beta 0.5

## Objetivo: sincronizar de dentro do Premiere, sem XML manual

Abra a sequência com as câmeras e áudios, clique em **Sincronizar e criar sequência** no painel do ReSync e aguarde. O painel exporta temporariamente o XML, inicia o motor Python do ReSync.app no Mac, aguarda o resultado e importa o XML sincronizado automaticamente como **uma NOVA sequência dentro do projeto aberto**. A timeline original não é modificada.

O XML continua existindo como transporte interno; o usuário não precisa abrir o app nem exportar/importar manualmente. O motor **reutiliza pistas livres de vídeo/áudio compatíveis**, preservando o sincronismo.

O [plano do plugin independente](../docs/PREMIERE_INDEPENDENTE.md) separa o beta
atual da preparação do runtime embutido e do futuro instalador `.ccx`.

### Fluxo interno

1. UXP `ProjectConverter.exportAsFinalCutProXML` exporta a sequência ativa para uma pasta autorizada pelo usuário.
2. UXP cria um arquivo `resync_<ID>.job.json` com o protocolo v1 e chama `shell.openExternal('resync://sync?job=...')`.
3. O ReSync.app instalado no macOS registra o esquema `resync://`. O bridge executa `resync-server --resync-job /pasta/job.json` como processo separado, preferencialmente sem abrir a janela.
4. O motor lê o XML, analisa o áudio original localmente e publica `resync_<ID>.status.json` de forma atômica. Uma falha não gera XML importável.
5. Após conclusão, o motor grava `resync_<ID>.synced.xml` com nome de sequência exclusivo.
6. UXP confirma a identidade do projeto, chama `project.importFiles([xmlPath], true, null, false)` e identifica a sequência pelo **novo GUID e nome esperado** antes de abri-la. Sequências preexistentes podem ter o mesmo nome; elas não são confundidas com o resultado. Os clipes da timeline original permanecem onde estavam.

**Validação no Premiere:** o fluxo da Beta 0.4 já criou uma segunda sequência no Premiere 26.5.2 real. As proteções novas da Beta 0.5 passaram nos testes simulados e ainda precisam de validação no host. Caso o Premiere recuse o XML, o arquivo gerado fica na pasta e o painel mostra um erro; não há importação falsa.

### Requisitos

- macOS Apple Silicon com **ReSync.app 0.6.8 ou posterior**, incluindo o bridge `resync://`. A 0.6.11 acrescenta a verificação adicional para separar músicas diferentes; confirme sua publicação antes de atualizar em ReSync.app → Preferências → Buscar atualizações. O painel UXP Beta ainda é instalado e atualizado separadamente.
- Premiere Pro 26.5.2.
- UXP Developer Tool com modo desenvolvedor.
- Primeira execução: selecionar uma pasta de trabalho (pode ficar num SSD). O token de acesso é lembrado para as próximas execuções.
- Confirmação do macOS ao permitir ao plugin iniciar o motor local através do esquema `resync://`. Pode ser lembrada pelo sistema.

### Instalação de desenvolvimento

1. Instale/extraia uma compilação **do ReSync.app com o bridge** e mova-o para a pasta Aplicativos.
2. Baixe o código do repositório do repositório atualizado e extraia-o.
3. Abra o **UXP Developer Tool** → Add Plugin → selecione `premiere-uxp/manifest.json` → Load & Watch.
4. Se você já carregou uma versão anterior do plugin, use **Unload** e remova o plugin antigo do UXP Developer Tool antes de adicionar o novo `manifest.json` da Beta 0.5. Depois abra o painel em Janela → UXP Plugins → ReSync.
5. Abra uma sequência que contenha as câmeras/áudios que deseja sincronizar.
6. Clique em **Sincronizar e criar sequência**.
7. Escolha uma pasta de trabalho na primeira vez. O plugin pode pedir autorização para abrir o ReSync.
8. Aguarde e confirme que surgiu uma **segunda sequência**, com sufixo ReSync, sem afetar a original.

### Se o macOS pedir para abrir um arquivo JSON, ou o painel parecer travado

Isso geralmente significa que o esquema `resync://` não foi associado à versão beta do ReSync.app. Não selecione arquivos JSON no diálogo: feche esse diálogo.

1. Clique em **Interromper espera** no painel, ou descarregue o plugin pelo UXP Developer Tool.
2. No Finder, abra **Aplicativos → ReSync → Obter Informações**. Confirme que a versão é **0.6.8** ou posterior. Se for anterior, use Preferências → Buscar atualizações no ReSync.app.
3. Inicie o ReSync.app uma vez pelo Finder e feche-o. Se o macOS bloquear a abertura, libere em Privacidade e Segurança após conferir a origem.
4. Reinicie o Premiere, carregue novamente o painel Beta 0.5 e tente com uma sequência de teste pequena.
5. Se não houver status do motor em aproximadamente 35 segundos, o painel mostrará erro e liberará o botão automaticamente (em vez de esperar 90 minutos).

A distribuição pública ainda não é notarizada pela Apple. A ponte `resync://` já criou uma sequência no Premiere 26.5.2 em teste do usuário; outros projetos e configurações ainda precisam de testes.

### Proteções da Beta 0.5

- A identidade usa as propriedades oficiais `Project.guid` e `Sequence.guid`, convertidas pelo método documentado `Guid.toString()`. O caminho de um projeto salvo também é capturado no início para detectar **Salvar como** durante a análise. Se o Premiere não fornecer um GUID válido, o painel pede para salvar e reabrir o projeto; não inicia o motor nem importa em um projeto cuja identidade seja desconhecida.
- **Interromper espera** passa a impedir o início do motor mesmo quando é acionado durante a escolha de pasta, exportação ou gravação do pedido. Se o motor já começou, ele pode continuar em segundo plano, mas o painel interrompido não importa o resultado.
- A importação começa somente após uma última conferência do projeto ativo. A partir desse ponto, a interrupção deixa de ser oferecida: `importFiles` é assíncrono e não expõe uma ação de desfazer transacional pela API UXP.
- O resultado mostra **takes com áudio confirmado / total** e quantos precisam de revisão em vermelho. Criar uma sequência não é tratado como confirmação de que todos os takes foram sincronizados. As referências também entram na contagem de áudio confirmado.
- Se não houver exatamente uma sequência nova com o nome esperado, o painel orienta a conferir o painel Projeto e mantém o XML na pasta de trabalho.

### Limitações conhecidas do beta

- **Já criou uma nova sequência real no Premiere 26.5.2**, mas o fluxo continua em Beta e testes Node não substituem avaliações com diversos materiais.
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

Os testes incluem preservação do XML original em sucesso/erro, estado do worker, simulação de exportação→lançamento→importação, GUIDs com nomes duplicados, troca de projeto, interrupção em cada etapa anterior à importação e apresentação de takes não confirmados.

### Documentação usada

- https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectconverter
- https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project
- https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequence
- https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/guid
- https://developer.adobe.com/premiere-pro/uxp/resources/recipes/external-process/
- https://developer.adobe.com/premiere-pro/uxp/resources/recipes/filesystem-operations/

