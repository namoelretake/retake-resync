# ReSync Experimental — busca de áudio e alinhamento

O motor experimental reutiliza as assinaturas de áudio calculadas pelo ReSync.
Em projetos com muitos takes, procura sequências de eventos acústicos que votem
no mesmo deslocamento e só então faz a correlação completa e a confirmação
tonal. Projetos pequenos, índices densos e takes sem confirmação forte recorrem
à busca completa. Referências longas continuam sendo comparadas contra os takes.
O alinhamento pode refinar pequenos desvios apoiados por vários pares, sem unir
ou separar blocos por conta própria. Nenhuma mídia ou projeto original é alterado.

O modo de produção permanece estável por padrão. Ative o experimental com
`RESYNC_ENGINE=next` ou `sync(..., engine_mode="next")`. A compilação local
ReSync Experimental usa esse motor, um identificador de app e um cache próprios.
Ela não registra a ponte do Premiere e não usa o canal de atualização estável.
O fluxo de teste é importar XML de uma cópia do projeto e exportar a sequência
sincronizada.

## Medição local

Mídia sintética, Apple Silicon, FFmpeg 7.1 empacotado, NumPy 2.5.4, até quatro
decodificações em paralelo. A primeira rodada usa cache do ReSync vazio; o
número repetido é a mediana de seis rodadas com cache. Sem limpar o cache do
sistema. Mede motor e geração do XML, sem leitura no Premiere.

| Cenário | Primeira: atual → experimental | Repetição: atual → experimental | Pares examinados |
| --- | ---: | ---: | ---: |
| 8 câmeras, 3 min, AAC | 0,520 → 0,438 s | 0,044 → 0,036 s | 28 → 28 |
| 60 takes dispersos | 0,524 → 0,362 s | 0,110 → 0,033 s | 1.770 → 62 |
| 120 takes dispersos | 1,342 → 0,875 s | 0,432 → 0,069 s | 7.140 → 120 |
| 240 takes dispersos | 5,417 → 1,573 s | 3,235 → 0,134 s | 28.680 → 241 |
| Gravador contínuo e 24 takes | 0,577 → 0,489 s | 0,162 → 0,187 s | 300 → 48 |

O XML foi idêntico em todos os cenários; os offsets e a quantidade de blocos
também foram conferidos com os valores esperados. O caso com gravador contínuo
foi mais lento na repetição apesar da redução de pares: custo por par varia com
o tamanho das gravações, e preparar o índice também custa tempo. O caso de oito
câmeras usa busca completa e não demonstra redução do minuto relatado com mídia
real.

Exemplo de reprodução a partir da raiz do repositório, com FFmpeg no PATH:

```sh
git show 7bfa772:source/engine.py > /tmp/resync-stable.py
python build/benchmark_sync.py --baseline /tmp/resync-stable.py \
  --candidate-mode next --allow-validated-differences --scenario many-240 --repeats 7
```

O índice é aproximado. Um par ausente entre dois takes já confirmados pode
deixar de ligar blocos que deveriam estar conectados. O fallback cobre
isolados, evidência fraca e referências longas, mas não elimina essa
possibilidade em todas as gravações repetitivas ou com diferenças de microfone.
Antes de trocar o motor padrão, comparar em projetos representativos a
cobertura de pontes, os falsos merges e a latência da mídia ao resultado.
Os testes de músicas com ritmo parecido continuam obrigatórios.
