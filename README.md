# ReSync — by Retake

Sincronização multicâmera por áudio para editores de vídeo. Importa e exporta **Final Cut Pro 7 XML** compatível com fluxos de trabalho do Adobe Premiere Pro.

## ReSync para macOS

O aplicativo utiliza uma janela nativa AppKit + WebKit, com motor Python e FFmpeg empacotados. Você pode sincronizar takes de múltiplas câmeras sem depender do navegador ou abrir o Terminal para usar o aplicativo.

**Base do sincronizador:** versão 0.6.1. A versão experimental 0.6.2 não foi incorporada à base devido a problemas identificados com WAVs avulsos. A versão do aplicativo 0.6.3 adiciona a estrutura do atualizador, mas não modifica o algoritmo principal de sincronização.

## Código

- `source/engine.py` — áudio, correlação, organização e exportação XML.
- `source/app.py`, `source/index.html` — interface local e endpoints de processamento.
- `source/tests/` — testes automatizados.
- `native/ReSyncNative.swift` — janela macOS e ponte com Sparkle.
- `build/` — compilação, empacotamento e geração de feed de atualização.
- `assets/` — ícone e demais recursos.

## Testes

No ambiente com Python 3 e FFmpeg no PATH:

```bash
python3 -m pip install -r source/requirements.txt
python3 -m unittest discover -s source/tests -p 'test_*.py' -q
```

O motor analisa até quatro segmentos independentes em paralelo, reutiliza
transformações durante as comparações e mantém um cache local de assinaturas
para novas sincronizações da mesma mídia. As comparações e os critérios de
confirmação de áudio permanecem completos. Veja [medição e cache](docs/DESEMPENHO.md).

## Compilação

No macOS, com Swift e Python 3.10+ disponíveis:

```bash
bash 'Gerar aplicativo independente.command'
```

Os arquivos são gerados na pasta `saida/`. O pipeline manual de GitHub Actions em `.github/workflows/macos-build.yml` também gera um aplicativo de teste não notarizado.

## Atualizações automáticas

A partir da versão 0.6.3, o ReSync inclui **Preferências → Buscar atualizações** usando Sparkle. O pacote só consegue instalar atualizações quando existe uma chave EdDSA pública incorporada ao app e uma versão mais nova publicada em GitHub Releases com assinatura correspondente.

Veja [a preparação e publicação do canal de atualizações](docs/ATUALIZACOES.md).

## Limitações atuais

- A correspondência com determinados WAVs finalizados sem vídeo vinculado ainda não está validada para todos os materiais reais.
- É recomendado conferir a sequência XML em uma cópia do projeto Premiere antes de usar em produção.
- Versões sem Apple Developer ID e notarização podem exigir autorização manual do macOS.

## Licenciamento e privacidade

A disponibilização pública deste código não representa, por si só, uma concessão de licença para redistribuição. Licenças de componentes como FFmpeg e Sparkle devem ser respeitadas na publicação de instaladores.

Os vídeos e áudios são processados localmente. Não envie material de clientes ao repositório.

