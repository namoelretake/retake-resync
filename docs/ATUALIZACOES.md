# Plano de atualizações automáticas

O ReSync ainda **não** possui atualizações automáticas.

Etapas:
1. Publicar e testar o código-base 0.6.1.
2. Compilar e validar o .app no Apple Silicon.
3. Assinar com Apple Developer ID e notarizar o aplicativo.
4. Implementar sistema seguro de distribuição e atualização.
5. Testar atualização, retorno à versão anterior e falhas de rede.

**Segurança:** não colocar token de GitHub dentro do aplicativo. Releases privados não funcionam como um feed público anônimo.
