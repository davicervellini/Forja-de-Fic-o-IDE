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

## Projeto novo: árvore de escolhas

Ao criar um projeto, o assistente (`gui_akashic.AkashicWizard`) percorre uma árvore em cascata (`pipeline/akashic_tree.py`).
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

O botão **📜 Akáshico** abre o editor: abas Universos, Personagens e Texto. **Salvar** grava o arquivo, mantém uma cópia
`registro_akashico.md.bak` e recompila o `registro_modelo.md`. Arquivo no formato antigo mostra um aviso com o botão
**Converter para o formato v2**, que guarda `registro_akashico.v1.bak.md` antes.

## Migração de um arquivo v1

`pipeline/akashic_migrate.py` não reescreve o corpo: lê os universos da seção 9.5 e os personagens da seção 5 e insere o
bloco de metadados. Também cadastra como reserva os universos do catálogo (`pipeline/akashic_catalog.py`) que ainda não
estavam na história, para a importação de wiki poder usá-los sem alterar a lista fechada.

## Wiki

A importação de wiki lista os universos do Akáshico do projeto (ativos primeiro, reserva marcada). Sem Akáshico v2, usa o
dicionário antigo. O subdomínio de cada universo é editável na aba Universos.

## Testes

`python -m pytest tests`
