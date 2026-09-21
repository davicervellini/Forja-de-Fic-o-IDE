# Como rodar a Forja de Ficção com a bíblia

Guia de uso do pipeline em Python desta pasta, `gui.py`, `main.py` e `pipeline/`, junto com a bíblia. O pipeline injeta a bíblia inteira no prompt de rascunho, mais os resumos acumulados e a premissa do capítulo. Isso define as regras abaixo.

## 1. Carregar biblia-modelo.md, não a bíblia completa

- `biblia-do-mundo.md` é o documento do autor. Tem perto de 40 mil caracteres, o que dá mais de 11 mil tokens em português. Não cabe no contexto de 8192 configurado no `.env`. Quando o prompt passa do limite, o Ollama corta o começo em silêncio, o aviso só aparece no log do servidor, e o modelo escreve sem as regras.
- `biblia-modelo.md` é a versão curta, gerada por `build_biblia_modelo.py` a partir das seções 1, 2, 5.8, 9.5, 10, 13.2 e 14 da bíblia completa. É a que o pipeline deve carregar.
- Na interface: usar o botão de selecionar a bíblia no painel lateral e escolher `biblia-modelo.md`.
- Na linha de comando: em `pipeline/config.py`, trocar `biblia-do-mundo.md` por `biblia-modelo.md` na linha de `WORLD_BIBLE_PATH`.
- Depois de qualquer edição na bíblia completa:

```bash
python build_biblia_modelo.py
```

## 2. Contexto: o que o .env tem hoje e o que precisa

Valores atuais: `DRAFTING_NUM_CTX=8192`, `REFINING_NUM_CTX=8192`, `SUMMARIZING_NUM_CTX=4096`.

Orçamento do prompt de rascunho, em tokens, contando 3 caracteres por token em português:

| Bloco | Tokens |
|---|---|
| System prompt do rascunho | 400 |
| biblia-modelo.md, com as fichas preenchidas | 3.000 a 3.500 |
| Resumos acumulados | 400 a 700 por capítulo anterior |
| Premissa do capítulo | 200 a 400 |
| Saída, capítulo de 2.000 palavras | 3.500 a 4.500 |

- Com 8192, o capítulo 1 cabe com pouca folga. A partir do capítulo 3 ou 4 os resumos acumulados empurram o prompt para fora do limite.
- Recomendado: `DRAFTING_NUM_CTX=16384`, `REFINING_NUM_CTX=12288`, `SUMMARIZING_NUM_CTX=8192`.
- O resumo com 4096 não cabe um capítulo de 2.000 palavras. Hoje o resumo só enxerga o fim do capítulo.
- Memória de vídeo: llama3.1:8b usa perto de 5 GB mais 1 GB de cache em 16k de contexto. gemma3:12b usa perto de 8 GB mais 2 GB em 12k. Com 8 GB de VRAM o gemma vaza para a RAM e fica lento; com 12 GB roda; com 16 GB sobra. A resposta de P55 define os números finais.

## 3. Formato da premissa por capítulo

Um arquivo por capítulo em `premissas/`, ordenados pelo nome: `01_a_morte.txt`, `02_atlantis.txt`. O pipeline acrescenta sozinho a instrução de desenvolver a premissa em capítulo, então o arquivo é uma lista de cenas, não prosa.

```
Capítulo 01: A morte e o acordo
Objetivo: uma frase com o que o capítulo precisa entregar.
Personagens em cena: nomes exatamente como na bíblia.
Universos em cena: só os da lista da bíblia.
Regras em foco: números da seção de regras invioláveis.
Cenas, na ordem:
1. Lugar. Quem. O que acontece. O que muda.
2. ...
3. ...
Termina com: gancho ou decisão.
Precisa aparecer: lista curta de fatos.
Não pode aparecer ainda: lista curta.
Tamanho: cerca de 2.500 palavras.
```

## 4. Fluxo recomendado

1. Responder o `questionario.md`. Com as respostas, a bíblia é preenchida e as marcações `[A DEFINIR]` somem.
2. Rodar `python build_biblia_modelo.py`. O script avisa se ainda houver marcação pendente.
3. Escrever as premissas da temporada inteira em `premissas/`.
4. Rodar a temporada em um único lote, pela interface com "Carregar Pasta de Premissas" ou por `python main.py --batch premissas/`. A continuidade entre capítulos só existe dentro de uma execução, ver a seção 5.
5. Conferir cada `2_capitulo_final.md` contra a seção 2 da bíblia antes de aceitar. O polimento não corrige quebra de regra, só estilo.
6. Fato novo que surgiu no capítulo entra na bíblia completa, e o script roda de novo.

## 5. Limites do pipeline hoje e ajustes sugeridos

Nada disto foi alterado no código. São observações da leitura de `pipeline/` e `gui.py`, com o ajuste que eu faria em cada caso, dependendo da resposta de P54.

- **Contexto acumulado vive só na memória.** `output/contexto_acumulado.md` é gravado, mas nunca lido de volta. Fechar o programa e rodar o capítulo 5 no dia seguinte gera um novo `capitulo_01`, por cima do antigo e sem memória dos anteriores. Ajuste: carregar o arquivo no início e aceitar o número do capítulo inicial; `run_batch` já tem o parâmetro `start_from`, a interface só não o usa.
- **Resumos crescem sem limite.** O prompt de resumo pede seis seções e manda não omitir nada; cada resumo sai com 500 a 800 tokens. Em dez capítulos são 6 mil tokens só de resumo. Ajuste: limitar cada resumo a 200 palavras, ou manter completos só os três últimos e um resumo geral dos anteriores.
- **Polimento não vê a bíblia.** O gemma3:12b recebe só o rascunho e as instruções de editor. Pode trocar grafias e nomes. Ajuste: passar a lista de grafias oficiais e o guia de estilo em `build_refining_prompt`.
- **Polimento e guia de estilo apontam para lados opostos.** O prompt do editor pede prosa rica, sensorial e literária; o guia de estilo da bíblia propõe frases curtas e detalhe concreto. Decidir a direção em P56 e alinhar `SYSTEM_REFINING`.
- **Rascunho curto.** O prompt pede pelo menos 2.000 palavras; o llama3.1:8b costuma parar antes. Se acontecer, dividir a premissa em duas metades e rodar como dois capítulos que depois viram um.
- **Resumo com llama3.1:8b.** Funciona, mas o gemma3:12b resume com menos invenção. Custa uma troca de modelo a menos, já que o gemma acabou de rodar o polimento.
