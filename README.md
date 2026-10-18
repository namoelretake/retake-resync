# ReSync — by Retake

ReSync é um aplicativo para sincronização multicâmera por áudio, usando XML do Final Cut Pro 7 para importar e exportar sequências no Adobe Premiere Pro.

**Versão-base:** 0.6.1 Beta. Código privado. A versão 0.6.2 (tentativa de melhoria com WAVs independentes) não foi considerada estável.

## Componentes

- `source/engine.py`: algoritmo de sincronização e exportação XML.
- `source/app.py` e `source/index.html`: interface e processamento local.
- `source/tests/`: testes automatizados.
- `native/ReSyncNative.swift`: janela do aplicativo macOS (AppKit/WebKit).
- `build/`: compilação do aplicativo e DMG.
- `assets/`: ícones do aplicativo.

## Executar os testes

```bash
python3 -m pip install -r source/requirements.txt
python3 -m unittest discover -s source/tests -p 'test_*.py' -q
```

## Construir o app

No macOS, com Python 3 e Apple Command Line Tools, executar `Gerar aplicativo independente.command`. O script prepara um pacote local `saida/ReSync.app` e tenta criar `saida/ReSync-macOS.dmg`.

O aplicativo final ainda precisa ser compilado e validado em macOS, assinado e notarizado para distribuição profissional.

## Problemas conhecidos

- Alguns WAVs de áudio final, sem vídeo vinculado, não sincronizam adequadamente no material real da Retake.
- Verifique o XML exportado em uma cópia do projeto do Premiere.
- **Atualização automática do app ainda não está implementada.** GitHub Actions prepara apenas testes e compilação, não instala atualizações no Mac.

**Privacidade:** não envie materiais originais de clientes a este repositório.
