# ReSync para Premiere: caminho para um plugin independente

## Diagnóstico em 20/10/2026

O painel UXP existente já automatiza a exportação temporária, o processamento e a
importação de uma nova sequência. O usuário não precisa manipular XML. Esse fluxo
foi exercitado no Premiere 26.5.2; a sequência original é preservada. Os testes
simulados não demonstram compatibilidade com todos os projetos e efeitos.

A dependência atual está no lançamento do motor: o painel abre `resync://`, que é
registrado pelo ReSync.app instalado. O motor usa Python, NumPy e FFmpeg; um painel
UXP JavaScript comum não os executa como módulos JavaScript. A API `shell` abre
arquivos ou protocolos, mas não oferece um executor geral com argumentos e saída
capturada. [Receita oficial de processos externos](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/external-process/).

## Decisão: plugin híbrido com o mesmo motor

O caminho escolhido para o protótipo independente é um plugin UXP híbrido:

1. O painel continua responsável pela interface e pelas APIs do Premiere.
2. Um pequeno módulo C++ inicia exclusivamente o motor que acompanha o pacote.
3. O motor Python/NumPy e o FFmpeg ficam dentro da instalação do plugin. A análise
   roda fora do processo do Premiere, sem abrir uma janela do ReSync.app.
4. O painel recebe o resultado e cria/abre uma nova sequência.

A Adobe documenta suporte a plugins híbridos no Premiere 26.2 ou posterior.
Esses plugins carregam uma biblioteca `.uxpaddon` por `require()`. O SDK oficial
é obtido em Downloads no Adobe Developer Console, com login.
[Guia oficial de plugins híbridos](https://developer.adobe.com/uxp/guides/how-to/hybrid-plugins/).

Essa abordagem mantém o algoritmo atual, a extração paralela, o cache e a
confirmação de identidade musical. O empacotamento inclui o próprio Python:
o cliente não precisa instalar Python, NumPy, Homebrew ou FFmpeg.

O componente interno de processamento é parte do plugin; a meta de distribuição
é uma instalação única, sem instalar ou abrir o ReSync.app separadamente.

## Entregas e critérios de conclusão

### 1. Preparar e testar o motor empacotado

- Entrada dedicada de processamento, sem servidor HTTP, janela, Sparkle ou
  associação `resync://`.
- Pacote de desenvolvimento contendo o executável, suas bibliotecas e FFmpeg.
- Versão, arquitetura e protocolos consultáveis antes de executar um trabalho.
- Teste do executável congelado com áudio sintético e geração de XML, incluindo
  duas músicas diferentes que devem continuar em blocos separados.
- Lançador C++ com executável fixo, argumentos separados e acompanhamento do
  processo. Não executar comandos de texto por shell.

O pacote dessa etapa é um **runtime de desenvolvimento**, não um plugin
independente instalável. O painel Beta 0.5 continua usando a ponte existente.

Validação desta etapa no Mac Apple Silicon: 94 testes Python do motor/worker,
36 testes simulados do painel e 11 testes do lançador C++ aprovados. O runtime
PyInstaller real também foi iniciado pelo lançador nativo com quatro takes
sintéticos: confirmou dois pares de câmeras, manteve duas músicas em blocos com
dez segundos de intervalo, preservou XML/mídias originais e recolheu o processo
ao terminar. Esse ensaio não é um teste de carregamento do addon no Premiere nem
uma medição de desempenho com material de clientes.

As instruções de compilação estão em [build/premiere](../build/premiere/README.md).
O workflow manual `premiere-runtime.yml` pode gerar esse runtime experimental,
sem publicar atualização do aplicativo ou competir automaticamente pela fila de
compilação das releases.

### 2. Conectar o módulo híbrido ao painel

- Obter o SDK oficial; não reproduzir uma ABI presumida nem incluir cópias de
  terceiros do SDK no repositório.
- Compilar o adaptador `.uxpaddon` e derivar a pasta do runtime a partir da
  localização do próprio módulo nativo, sem aceitar um executável arbitrário
  fornecido pelo JavaScript.
- Usar manifesto 6, `addon` e permissão `enableAddon`, com binários na estrutura
  de plataforma/arquitetura exigida pela Adobe.
- Administrar os identificadores de processos até o término; liberar o módulo
  deve encerrar e recolher trabalhos ainda ativos de maneira definida.
- Testar no Premiere real com um projeto sintético, sem depender de ReSync.app
  instalado. Verificar cancelamento, falhas, nomes duplicados, mudança de projeto
  e preservação da sequência original.

[Construção e requisitos de um plugin híbrido](https://developer.adobe.com/uxp/guides/how-to/hybrid-plugins/build).

### 3. Reduzir a dependência do XML interno

O fluxo atual usa XML como transporte interno. Retirá-lo é uma entrega separada:
capturar mídias e tempos pelas APIs do Premiere, obter um plano de sincronização
do mesmo motor e aplicar esse plano somente em uma nova sequência.

As APIs documentadas incluem clonagem de sequência e ações de alteração de
clipes, mas a preservação de links, efeitos, áudio multicanal, velocidades e
transições precisa de provas no host. A existência dessas APIs não comprova
automaticamente que uma implementação nova preserva esses detalhes. Não ativar
um fluxo nativo sem testar esses casos.
[Sequence](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequence/),
[VideoClipTrackItem](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/videocliptrackitem/),
[SequenceEditor](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor/).

### 4. Distribuir em um instalador

Depois de validar o plugin integrado, gerar um `.ccx` pela ferramenta oficial
UXP Developer Tool e testar a instalação pelo Creative Cloud. Um ZIP de código
fonte ou runtime renomeado para `.ccx` não constitui essa validação.

O primeiro alvo será macOS Apple Silicon. Para distribuição híbrida de produção
no Mac, a Adobe exige Developer ID e notarização dos módulos nativos. A publicação
no Marketplace também exige as arquiteturas previstas pela Adobe; uma prévia
independente pode oferecer apenas as arquiteturas efetivamente testadas.
[Construção e distribuição híbrida](https://developer.adobe.com/uxp/guides/how-to/hybrid-plugins/build),
[instalação oficial de plugins](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/install/).

## Limites desta entrega

Não há ainda adaptador `.uxpaddon` compilado com o SDK oficial, instalação `.ccx`
validada ou teste do runtime embutido iniciado pelo Premiere. A preparação do
motor não remove, sozinha, a dependência do ReSync.app no painel existente.
O aplicativo macOS e seu canal de atualização continuam sendo produtos separados.
