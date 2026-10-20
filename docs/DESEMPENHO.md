# Desempenho da sincronização

Esta otimização mantém todos os pares de gravações independentes, a resolução
de 50 assinaturas por segundo, os critérios de confiança e o posicionamento
dos grupos. Não pula comparações para aparentar rapidez.

## O que mudou

- Extração de segmentos independentes em até quatro tarefas simultâneas.
  Segmentos idênticos aparecem uma única vez na fila; o grafo continua sendo
  construído na ordem original, independentemente da ordem das tarefas.
- Reutilização das FFTs e energias por sincronização, com orçamento de 128 MiB
  e 256 entradas. Gravações grandes demais para o cache seguem pelo cálculo
  normal. O cache é liberado também em caso de erro.
- Cache persistente local de assinaturas para novas tentativas. A identidade
  inclui mídia, trecho, parâmetros de extração, versão do cache e executável
  FFmpeg. Alterar mídia ou decoder invalida a análise correspondente.
- Caminhos canônicos calculados uma vez por grupo, em vez de consultar o
  sistema de arquivos em cada comparação.

## Cache local

No macOS: `~/Library/Caches/ReSync/audio-features`. Em outros sistemas usa
`$XDG_CACHE_HOME/ReSync/audio-features` ou `~/.cache/ReSync/audio-features`.
São assinaturas numéricas, sem cópias do áudio, XML ou caminhos em claro. Os
nomes são hashes SHA-256. Apagar a pasta apenas obriga uma nova extração.

O orçamento de arquivos de assinatura é 512 MiB por cache; os mais antigos
são descartados ao salvar. Processos concorrentes podem ultrapassá-lo
temporariamente. Arquivos temporários próprios abandonados há mais de 24h
são removidos durante a manutenção. Leituras verificam cabeçalho, tamanho,
tipo, valores finitos e checksum; corrupção ou indisponibilidade do cache
fazem o motor extrair novamente, sem impedir a sincronização. A gravação é
atômica e não persiste análise de mídia alterada durante a extração.

Aumentar `AudioCache.CACHE_VERSION` se mudar qualquer cálculo de assinatura.
Os arquivos `.npy` do cache têm um rodapé de 32 bytes com o SHA-256 do payload.

Variáveis para desenvolvimento e diagnóstico:

- `RESYNC_CACHE_DIR`: pasta alternativa para o cache de assinaturas.
- `RESYNC_DISABLE_CACHE=1`: desabilita o cache persistente.
- `RESYNC_WORKERS=1`: extração serial; valores entre 1 e 4 controlam o máximo
  de tarefas. O número de processadores e de segmentos também limita a fila.

## Medição reproduzível

Execute a partir da raiz do repositório, com as dependências e FFmpeg:

```sh
python build/benchmark_sync.py --scenario user-8x180 --media-format aac
python build/benchmark_sync.py --scenario many
python build/benchmark_sync.py --scenario long
```

Para comparar com a base anterior, salve o motor de `b442f7a` fora do diretório
source e informe seu caminho em `--baseline`. Por exemplo:

```sh
git show b442f7a:source/engine.py > /tmp/resync-engine-before.py
python build/benchmark_sync.py --scenario user-8x180 --media-format aac \
  --baseline /tmp/resync-engine-before.py --output /tmp/resync-benchmark.json
```

O cenário `user-8x180` contém oito gravações de três minutos com offsets
conhecidos e áudio de câmera vinculado. O benchmark gera mídia sintética,
mede parse, extração, comparação e exportação, valida offsets e exige igualdade
do resultado e XML com a base. A primeira passagem usa cache ReSync vazio; a
segunda usa a mesma mídia. AAC usa 48 kHz estéreo a 192 kbps; WAV usa PCM mono
a 16 kHz. A preparação/compactação da mídia não entra no tempo medido.

O cache de arquivos do sistema operacional não é limpo. Os números medem o
motor e exportação; não incluem inicialização do app, exportação/importação
no Premiere, leitura de vídeos de clientes, efeitos ou latência de discos
externos. Ganho da correlação não equivale ao ganho do fluxo completo.

O workflow de testes publica um relatório sintético, sem impor tempo máximo
dependente do runner. Mudanças em `source/**` também acionam a compilação
macOS e seu teste do motor empacotado.
