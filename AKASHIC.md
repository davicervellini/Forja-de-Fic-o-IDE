# Registro Akáshico (formato v2)

O Registro Akáshico é o `registro_akashico.md` de cada projeto: a bíblia do mundo, fonte única de verdade da história.
O `registro_modelo.md` é a versão curta que os modelos recebem, gerada a partir dele (seções 1, 2, 5.8, 9.5, 10, 13.2 e 14).

## Formato

Continua sendo Markdown editável à mão. A versão 2 acrescenta, logo depois do título, um bloco de metadados em
comentário HTML (o Markdown ignora, e o modelo curto não o inclui):

```
# Bíblia do Mundo: Título
<!-- akashic:meta
{ "version": 2, "structure": "multiverse", "universes": [...], "characters": [...] }
-->
```

- `structure`: `single` (universo único), `multiverse` ou `timelines` (linhas do tempo alternativas).
- `universes[]`: `id`, `name`, `role` (base, source, visited, hub, original), `wiki` (subdomínio do Fandom),
  `active` (falso = reserva: cadastrado, mas fora da lista fechada da história), `allowed_characters`.
- `characters[]`: personagens principais (`protagonist`, `supporting`, `antagonist`).

Seções numeradas padrão: 1 Story summary (EN) · 2 Inviolable rules (EN) · 3 Premissa · 4 Sistema de poder ·
5 Personagens (5.8 Cast (EN)) · 6 Arcos · 7 Locais e organizações · 8 Cenário central · 9 Universos (9.5 Universes (EN)) ·
10 Style guide (EN) · 11 Estrutura da história · 12 Linha do tempo · 13 Glossário (13.2 Official spellings (EN)) ·
14 What the models must not do (EN). `[A DEFINIR: ...]` marca o que ainda falta preencher.

## O que vai para o modelo

O `registro_modelo.md` junta as seções 1, 2, 5.8, 9.5, 10, 13.2 e 14 e também **qualquer seção com "(EN)" no título**
(é a convenção para o que é escrito para o modelo). A cada capítulo o pipeline recorta esse arquivo
(`pipeline/canon.py`, resultado em `capitulos/capitulo_NN/canon_modelo.md`):

- **Fichas (5.8)**: só do elenco da premissa, dos protagonistas e de quem já estreou e é citado na premissa ou na
  memória. Quem estreia depois não aparece nem de nome, nem nas frases da seção 1. A estreia vem do campo
  **Estreia (capítulo)** do personagem; sem ele, do que a ficha disser ("Visitor from Chapter 205", "Created in
  Chapter 5"); sem isso, de ter estado no elenco planejado de um capítulo já concluído.
- **Universos (9.5)**: os dos personagens que entraram, dos locais em cena, os de papel base/hub e os citados.
- **Grafias (13.2)**: só os nomes que o capítulo usa, com a maiúscula do registro ("the heart of the city" não puxa
  "the Heart").
- **Seções com faixa de capítulos no título** entram só nessa faixa. Exemplo, para fatos de um arco que as premissas
  usam e que o resto do registro não diz:

  ```
  ### 6.9 Arc card: Atlantis, Chapters 1-3 (EN)
  - Atlantis rests on the ocean floor of Lantea, an ocean planet in the Pegasus galaxy. Never the Atlantic.
  - Transporters are closet-sized cabins with a city-map panel.
  ```

  "Chapters 6+" vale do capítulo 6 em diante. Com um cartão de arco valendo, a seção 1 vai só com o primeiro parágrafo
  e o de "Timing".
- **Amostra de voz** (`### 10.1 Voice sample (EN)`): dois trechos curtos de um capítulo bom, com os parágrafos. Sai do
  bloco do registro e vai para o fim do prompt de cada cena; frases copiadas dela são apagadas do texto gerado.
- **Glossário** (seção com "Glossary" ou "Glossário" no título): linhas `senha = the ticket (queue number, Chapter 1)`.
  A tradução da premissa usa esses pares, e um termo em português que vaze para a história faz a cena ser reescrita.
  Frases "Nome e grafia oficial: a Gerência. Em inglês, the Management." em qualquer lugar do registro também valem.

O personagem também tem o campo **Voz** (em inglês): regras curtas de fala e duas ou três falas reais. Vai no fim do
prompt de cada cena em que ele aparece; sem ele, vai a ficha.

## Projeto novo: árvore de escolhas

O assistente de criação percorre uma árvore em cascata (`pipeline/akashic_tree.py`). A tela dele era da interface antiga,
que foi removida; a lógica continua aqui, testada, para a interface web.
Cada pergunta só aparece quando as respostas anteriores a tornam relevante:

1. Origem do mundo (original, fanfic, crossover) e estrutura (um universo, multiverso, linhas do tempo).
2. Multiverso: como os universos se conectam, se a lista é fechada, quais universos entram e com que papel.
3. Linhas do tempo: ponto de divergência. Fanfic/crossover: política de cânone e época de início.
4. Sistema de poder (jogo, magia, tecnologia, superpoderes...), regras de jogo, convivência de poderes entre mundos, morte.
5. Elenco (protagonistas, apoio, antagonismo), tom, romance, classificação, ponto de vista, idioma, tamanho dos capítulos.

Os eixos foram tirados de como as grandes séries organizam o próprio cânone: estrutura do mundo, conexão entre mundos,
lista de universos com regras, sistema de poder e seus limites, regras de vida e morte, elenco e estilo.
`pipeline/akashic_builder.py` transforma as respostas no arquivo v2.

## Edição dentro do projeto

A tela **Personagens e universos** edita universos, personagens e locais (os metadados) e gera as listas 5.8 e 9.5;
a tela **Registro Akáshico** edita o texto inteiro. Salvar recompila o `registro_modelo.md` e guarda a versão anterior
em `registro_akashico.anterior.md`.

## Migração de um arquivo v1

`pipeline/akashic_migrate.py` não reescreve o corpo: lê os universos da seção 9.5 e os personagens da seção 5 e insere o
bloco de metadados. Também cadastra como reserva os universos do catálogo (`pipeline/akashic_catalog.py`) que ainda não
estavam na história, para a importação de wiki poder usá-los sem alterar a lista fechada.

## Wiki

A importação de wiki lista os universos do Akáshico do projeto (ativos primeiro, reserva marcada). Sem Akáshico v2, usa o
dicionário antigo. O subdomínio de cada universo é editável na aba Universos.

## Testes

`python -m pytest tests`
