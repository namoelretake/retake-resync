# Runtime independente do ReSync para Premiere

Primeira etapa do plugin híbrido: empacota o motor existente com Python, NumPy e FFmpeg. Não depende de ReSync.app, Homebrew, servidor HTTP, janela nativa, Sparkle ou protocolo `resync://`.

Este diretório não entrega um plugin instalável. O SDK oficial Adobe, o `.uxpaddon`, sua integração no painel e a validação dentro do Premiere são etapas posteriores. O launcher nativo poderá iniciar o worker com `posix_spawn` e argumentos separados.

## Compilar e verificar

Em um Mac Apple Silicon, com NumPy, PyInstaller e imageio-ffmpeg já disponíveis no Python de build:

```sh
.build-venv/bin/python build/build_premiere_runtime.py --output ../premiere-plugin-stage --smoke
.build-venv/bin/python build/test_premiere_runtime.py ../premiere-plugin-stage/mac/arm64/runtime
```

O builder não instala nem baixa dependências. `--python CAMINHO` seleciona outro ambiente já preparado; `--replace` substitui apenas um runtime gerado anteriormente pelo builder. O destino de staging fica separado dos arquivos do painel.

```text
mac/arm64/runtime/
  resync-premiere-worker/
    resync-premiere-worker
    _internal/...
  bin/ffmpeg
  runtime.json
  licenses/...
```

## Contrato do worker

```sh
runtime/resync-premiere-worker/resync-premiere-worker --version-json
runtime/resync-premiere-worker/resync-premiere-worker --job /pasta/resync_ID.job.json
```

- `--version-json`: JSON com versão do motor, protocolos, arquitetura, versões de dependências e hashes de proveniência.
- `--job`: protocolo 1 de `source/sync_job.py`; usa os arquivos `.source.xml`, `.status.json` e `.synced.xml` ao lado do job. O XML e as mídias originais são preservados.
- Códigos de saída: `0` concluído, `1` falha registrada pelo processamento, `2` argumentos, solicitação ou runtime inválidos.
- O caminho do FFmpeg é ancorado no próprio runtime. O worker substitui o `PATH` recebido; a ausência do binário empacotado causa erro, sem recorrer ao FFmpeg do sistema.

O smoke executa o binário congelado com `PATH` vazio e referências ambientais a aplicativos inexistentes. Confirma quatro takes de duas músicas distintas, o alinhamento legítimo de dois segundos entre suas câmeras, dez segundos entre músicas, XML e mídias originais intactos, e rejeição de runtime sem FFmpeg. Os sinais são gerados localmente; não há mídias privadas.

## Distribuição

`runtime.json` registra hashes do executável, FFmpeg e fontes. `licenses/` reúne os avisos disponíveis nas distribuições locais e as saídas originais de `ffmpeg -version` e `ffmpeg -L`. A licença do wrapper imageio-ffmpeg não deve ser confundida com a do executável FFmpeg.

O staging é um protótipo para testes locais. Não afirma que a revisão de redistribuição esteja concluída. Antes de publicação, revisar a composição exata do FFmpeg e demais dependências, completar materiais de licença/fontes aplicáveis e preparar assinatura/notarização dos componentes nativos. O SDK Adobe não é incluído neste runtime.
