# ⚒ Forja de Ficção — Pipeline Ollama

Pipeline automatizado de geração e refinamento de texto para ficção, consumindo a API REST local do **Ollama**.

## O que faz

O sistema orquestra um fluxo sequencial de três fases para cada capítulo:

| Fase | Modelo | Objetivo |
|---|---|---|
| **1. Rascunho** | `llama3.1:8b` | Construtor de Narrativas — desenvolve diálogos, sequência de eventos, base da cena |
| **2. Polimento** | `gemma3:12b` | Editor Chefe de Ficção — eleva qualidade literária, descrições sensoriais, peso emocional |
| **3. Resumo** | `llama3.1:8b` | Analista de Continuidade — extrai resumo para manter consistência entre capítulos |

O output de cada fase alimenta a próxima. Em modo **fila de capítulos**, o resumo de cada capítulo é acumulado e injetado no prompt do próximo, garantindo consistência narrativa.

## Pré-requisitos

1. **Python 3.11+** instalado
2. **Ollama** instalado e rodando: [ollama.com](https://ollama.com)

## Instalação

```powershell
# 1. Instalar dependências Python
cd d:\Projetos\Fanfiction
pip install -r requirements.txt

# 2. Puxar os modelos no Ollama
ollama pull llama3.1:8b
ollama pull gemma3:12b

# 3. Garantir que o Ollama está rodando
ollama serve
```

## Uso

### Abrir o programa

Para abrir sem a janela preta do console, use o atalho **Forja de Ficção** da área de
trabalho, dê dois cliques em `Abrir Forja.pyw` ou rode `pythonw app_desktop.py`. Sem
console, o log fica em `logs/forja.log`, e um erro na abertura aparece numa caixa de aviso.

O atalho aponta para `launcher/Forja de Ficção.exe`, uma cópia do `pythonw.exe` com o ícone
da Forja, para a barra de tarefas e os docks mostrarem o ícone certo em vez do Python. Ele é
gerado por `python tools/make_launcher.py` (a pasta fica fora do git); rode de novo depois
de atualizar o Python.

```powershell
python app_desktop.py            # janela própria (WebView2)
python app_desktop.py --browser  # abre no navegador padrão
```

A API local (`webapp/server.py`, FastAPI) roda só em 127.0.0.1. Nesta interface dá para
criar e abrir projetos, adicionar capítulos, gerar com o texto aparecendo ao vivo,
cancelar, editar o texto final, refazer um capítulo (com a premissa ajustada), atualizar
a memória depois de uma edição, ver e restaurar versões anteriores, editar a memória da
história e o Registro Akáshico em texto, exportar o livro (EPUB, Royal Road, Markdown, TXT, HTML) e
mudar as configurações. A tela **Personagens e universos** edita as fichas que vão para
o modelo (seções 5.8 e 9.5 do Registro), mostra em que capítulos cada personagem aparece
e lista quem surgiu na memória da história sem ficha no registro. O botão **Importar da
Wiki** busca personagens na wiki do Fandom do universo (campo Wiki na aba Universos), o
modelo da fase de resumo escreve a ficha no seu idioma e você revisa antes de ela entrar no
registro, com o universo de origem e a lista de permitidos. A aba **Locais** guarda os
lugares canônicos: ficha para o modelo (planta, o que existe ali, estado na época da
história e uma frase "Never:" com o que não existe no lugar), "fica dentro de",
"sempre no contexto" e as imagens da wiki. **Importar da Wiki** nessa aba busca a página
pelo nome exato, escreve a ficha com a época do universo, deixa escolher outra página e
sugere os sublocais listados na página. Na premissa, **Locais em cena** manda as fichas
desses locais para o fim de cada cena.

A aba **Premissa** tem um formulário guiado: título, objetivo, abertura (continuidade com
o capítulo anterior), personagens em cena, cenas com meta de palavras, gancho, o que
precisa e o que não pode aparecer. **✨ Sugerir com IA** escreve a premissa a partir do
plano da história (tabela da seção 11 do Registro), do fim do capítulo anterior e da
memória, e tira do elenco quem ainda não estreou; **🔎 Conferir** aponta contradições
antes de gerar. O elenco e as restrições da premissa vão reforçados no fim do prompt de
cada cena, e parágrafos de narração muito longos são quebrados no fim de uma frase.

Quando um capítulo termina, o programa já escreve a premissa do seguinte, com o fim do
capítulo fresco na memória. Essa premissa fica marcada com ✎ na lista e espera sua
revisão: ajuste na aba **Premissa** e clique em **✔ Aprovar e gerar**. Até lá, ▶ Gerar tudo
pula o capítulo. A opção fica em ⚙ Configurações. Premissa que já existe nunca é sobrescrita. Se a
premissa do capítulo seguinte já estiver escrita, a última cena do capítulo lê a abertura
dela e termina exatamente onde o próximo começa.

O programa trabalha com dois idiomas. O **idioma da interface** (⚙ Configurações) vale
para a tela e para o que o programa escreve só para você ler: fichas de personagens e
locais, sugestões e conferências de premissa, relatórios de verificação. O **idioma da
história** é escolhido em cada projeto (ao criar, ou em **Livro: idioma e exportação**) e
vale para o texto dos capítulos, o livro exportado e tudo o que o modelo lê de novo nos
capítulos seguintes: resumos, memória, roster e pontas soltas. Passar a memória pelo seu
idioma a cada capítulo trocava nomes oficiais e mudava o sentido dos fatos; para ler a
memória no seu idioma, use **Ler no meu idioma** na tela **História até agora** (a tradução
fica só na tela). Mudar o idioma da história não traduz capítulos prontos. A interface tem tradução para português e inglês; para
acrescentar outra, copie `webapp/static/i18n/en.json` para `<código>.json`, traduza os
valores e inclua o código em `UI_TRANSLATED` (`pipeline/languages.py`).

Para publicar no Royal Road, a aba **Texto final** de cada capítulo tem **Copiar título**
("Chapter 1: Tuesday", para o campo de título), **Copiar texto** (um parágrafo por bloco, com
itálico, para colar no editor) e **Copiar HTML** (o mesmo texto em código, para colar no botão
<> do editor, se a colagem normal falhar). Caixas [System] vão numa tabela de uma célula e a
quebra de cena é um "* * *" centralizado. A exportação **Royal Road** gera um .zip com um .html
por capítulo. Deixe "Ativar a Pasta Limpa" desligado: o texto já vem sem linhas vazias.

Toda edição, refação ou restauração guarda antes o texto anterior em
`capitulos/capitulo_NN/versoes/`.

**Como cada capítulo é escrito.** Antes da primeira cena:

- a premissa é traduzida para o idioma da história, com o glossário do registro (linhas
  `senha = the ticket` numa seção de glossário, ou "grafia oficial: X. Em inglês, Y") e o
  capítulo anterior como contexto. A tradução fica na aba **Verificações**, onde dá para
  corrigi-la; a correção vale enquanto a premissa não mudar;
- o Registro Akáshico é recortado para o capítulo: só as fichas de quem está nele (quem
  estreia depois nem é citado), os universos e as grafias que o capítulo usa e os cartões de
  arco da faixa de capítulos dele (veja `AKASHIC.md`). O recorte fica em `canon_modelo.md`;
- os itens de "Precisa aparecer" são distribuídos pelas cenas (`checklist.json`, editável),
  e os nomes proibidos e os personagens que ainda não estrearam viram trava.

Cada cena recebe um roteiro do capítulo em que as cenas seguintes aparecem só pelo nome, o
momento em que ela termina, os itens dela, as falas de exemplo do protagonista tiradas dos
capítulos já escritos e as linhas [System] que ainda cabem no capítulo. Cada cena é escrita
em **versões** (padrão 2, com o prompt em cache só a geração se repete); fica a de melhor
nota, e um juiz escolhe entre as duas melhores. A cena que traz quem não pode aparecer,
conversa com o leitor, troca de idioma ou esquece um item da premissa é escrita de novo com
a correção (até 2 vezes).

No polimento, primeiro uma **revisão com citação** aponta contradições, repetições, cenas
recontadas e erros de sentido, e só os trechos citados são trocados (`critica.md`). Depois o
polimento de linha recebe a lista exata do que reescrever (frases gastas, repetições de cenas
anteriores, aspas abertas) e a voz de quem está na cena. Polimento que traz gente nova ou
perde uma linha [System] volta só o parágrafo do rascunho; o que sai do texto por inteiro é
descartado.

No fim, a **conferência final** olha o capítulo inteiro, cena por cena, com o cânone do
capítulo e sem cortes. Se achar um problema grave (personagem que não pode estar ali, item
proibido, conversa de assistente), o capítulo fica marcado e a memória não é atualizada: a
geração em lote para ali. Revise na aba **Verificações**, corrija o texto ou refaça, e use
**↻ Atualizar memória**. A memória é atualizada por mudanças (`memoria_diff.md`): o que o
modelo não menciona fica como está, um thread só sai com evidência no resumo e nada que cite
quem ainda não estreou entra.

Tudo isso fica em ⚙ Configurações › Qualidade. Mais versões e conferências deixam o capítulo
mais lento. O prompt de cena tem um teto (`PROMPT_BUDGET_TOKENS` no `.env`); acima dele a
memória mais antiga é resumida antes. Os pedidos ao Ollama vão com `truncate: false`: se o
prompt não couber, o contexto cresce em vez de o começo do prompt sumir em silêncio.

Cada fase (rascunho, polimento, resumo) pode usar o Ollama local ou um modelo na nuvem:
Anthropic (Claude), Google (Gemini), OpenAI (GPT) ou qualquer serviço compatível com a
API da OpenAI (OpenRouter, por exemplo). As chaves de API são cadastradas em
⚙ Configurações e ficam em `credenciais.json` na pasta de dados do usuário, fora do git;
variáveis de ambiente como `ANTHROPIC_API_KEY` também valem. Na nuvem o texto da
história é enviado para o provedor e cada capítulo gasta créditos da conta.

**Reserva local.** Quando um provedor na nuvem esgota o limite de uso ou de crédito, fica
sobrecarregado ou para de responder, a geração continua no Ollama com o modelo de reserva
(padrão `gemma4:12b`) e a tela avisa. O provedor fica de lado por 30 minutos (ajustável) e
depois volta a ser tentado; **Voltar a usar a nuvem agora** em ⚙ Configurações encurta a
espera. Chave errada, modelo inexistente e recusa de conteúdo não trocam: aparecem como erro.
Os pedidos ao Ollama saem com `think: false`, porque modelos que raciocinam antes de
responder (gemma4, qwen3) gastariam o teto de tokens pensando.

## Configuração

A tela ⚙ Configurações grava o que você muda em `config.json`, na pasta de dados do usuário, e
isso vale mais que o `.env`. O `.env` da raiz traz os padrões e as chaves que a tela não mostra
(limites de memória, metas de cena, teto do prompt); apague uma linha para voltar ao padrão de
`pipeline/config.py`.

## Estrutura do Projeto

```
Forja-de-Fic-o-IDE/
├── app_desktop.py          ← Abre a janela (pywebview) com a interface
├── main.py                 ← O mesmo que app_desktop.py
├── .env                    ← Padrões da configuração
├── webapp/
│   ├── server.py           ← API local (FastAPI, só 127.0.0.1)
│   ├── jobs.py             ← Uma geração por vez, com eventos para a tela
│   └── static/             ← Interface (HTML, JS, traduções)
├── pipeline/
│   ├── orchestrator.py     ← Fases do capítulo: rascunho, polimento, resumo, memória
│   ├── prompts.py          ← Pedidos ao modelo
│   ├── canon.py            ← Pedaço do Registro Akáshico de cada capítulo
│   ├── qa.py               ← Conferência das cenas e do capítulo
│   ├── memory_ops.py       ← Memória atualizada por mudanças
│   ├── scenes.py           ← Cenas: divisão, limpeza, travas do polimento
│   ├── premise.py          ← Premissa guiada
│   ├── api.py, providers.py← Ollama e provedores na nuvem
│   └── ...
├── tests/                  ← pytest
└── projetos/<projeto>/     ← Uma pasta por história (fora do git)
    ├── registro_akashico.md, registro_modelo.md
    ├── estado.json, memoria_dinamica.md, roster.md, open_threads.md, callbacks.md
    └── capitulos/capitulo_NN/
        ├── premissa.md, premissa_modelo.md, checklist.json, canon_modelo.md
        ├── rascunho.md, critica.md, capitulo_final.md, resumo.md
        ├── consistencia.md, memoria_diff.md, polimento_descartado.md
        └── versoes/
```

## Tratamento de Erros

- **Ollama desligado:** mensagem clara pedindo para rodar `ollama serve`
- **Modelo não encontrado:** sugere `ollama pull <modelo>`
- **Timeout:** configurável no `.env` (padrão: 10 min para troca de VRAM)
- **Interrupção:** salva qualquer fragmento já gerado antes de encerrar
- **Erro no batch:** para a fila no capítulo com erro (não continua sem o resumo de continuidade)
