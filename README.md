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

### Interface nova (janela web, em construção)

```powershell
python app_desktop.py            # janela própria (WebView2)
python app_desktop.py --browser  # abre no navegador padrão
```

A API local (`webapp/server.py`, FastAPI) roda só em 127.0.0.1. Nesta interface dá para
criar e abrir projetos, adicionar capítulos, gerar com o texto aparecendo ao vivo,
cancelar, editar o texto final, refazer um capítulo (com a premissa ajustada), atualizar
a memória depois de uma edição, ver e restaurar versões anteriores, editar a memória da
história e o Registro Akáshico em texto, exportar o livro (EPUB, Markdown, TXT, HTML) e
mudar as configurações. O editor do Registro por abas, o assistente de criação e a
importação da wiki continuam na interface antiga até serem migrados.

Toda edição, refação ou restauração guarda antes o texto anterior em
`capitulos/capitulo_NN/versoes/`.

Cada fase (rascunho, polimento, resumo) pode usar o Ollama local ou um modelo na nuvem:
Anthropic (Claude), Google (Gemini), OpenAI (GPT) ou qualquer serviço compatível com a
API da OpenAI (OpenRouter, por exemplo). As chaves de API são cadastradas em
⚙ Configurações e ficam em `credenciais.json` na pasta de dados do usuário, fora do git;
variáveis de ambiente como `ANTHROPIC_API_KEY` também valem. Na nuvem o texto da
história é enviado para o provedor e cada capítulo gasta créditos da conta.

### Interface Gráfica antiga (customtkinter)

```powershell
python gui.py
```

Ou via CLI:

```powershell
python main.py --gui
```

### Linha de Comando — Capítulo Único

```powershell
# Usa premissa.txt automaticamente
python main.py

# Ou especifica o arquivo
python main.py --premise minha_premissa.txt
```

Se `premissa.txt` não existir, o script solicita a premissa via terminal.

### Linha de Comando — Fila de Capítulos (Batch)

Crie uma pasta com um `.txt` por capítulo (ordenados por nome):

```
premissas/
├── 01_chegada.txt
├── 02_exploracao.txt
└── 03_conflito.txt
```

```powershell
python main.py --batch premissas/
```

## Configuração

Edite o arquivo `.env` na raiz do projeto:

```env
# Modelos
MODEL_DRAFTING=llama3.1:8b
MODEL_REFINING=gemma3:12b

# Temperaturas (criatividade)
DRAFTING_TEMPERATURE=0.7
REFINING_TEMPERATURE=0.75

# Janela de contexto
DRAFTING_NUM_CTX=8192
REFINING_NUM_CTX=8192

# Timeout (segundos) — generoso para troca de modelo na VRAM
REQUEST_TIMEOUT=600
```

Na GUI, a configuração também pode ser editada em tempo real no painel lateral.

## Estrutura de Saída

```
output/
├── capitulo_01/
│   ├── 1_rascunho.md          ← Fase 1: narrativa bruta
│   ├── 2_capitulo_final.md    ← Fase 2: prosa polida
│   └── 3_resumo.md            ← Fase 3: resumo de continuidade
├── capitulo_02/
│   ├── ...
└── contexto_acumulado.md      ← Resumos acumulados de todos os capítulos
```

## Estrutura do Projeto

```
Fanfiction/
├── biblia-do-mundo.md      ← Referência do universo (cânone)
├── premissa.txt            ← Input principal (premissa do capítulo)
├── .env                    ← Configuração editável
├── requirements.txt        ← Dependências Python
├── main.py                 ← Entry point CLI
├── gui.py                  ← Interface gráfica
├── pipeline/
│   ├── __init__.py
│   ├── config.py           ← Carregamento de configuração
│   ├── api.py              ← Cliente da API do Ollama (streaming)
│   ├── io_utils.py         ← Leitura/gravação de arquivos
│   ├── prompts.py          ← System prompts das 3 fases
│   └── orchestrator.py     ← Lógica do pipeline (single + batch)
└── output/                 ← Saída gerada
```

## Tratamento de Erros

- **Ollama desligado:** mensagem clara pedindo para rodar `ollama serve`
- **Modelo não encontrado:** sugere `ollama pull <modelo>`
- **Timeout:** configurável no `.env` (padrão: 10 min para troca de VRAM)
- **Interrupção:** salva qualquer fragmento já gerado antes de encerrar
- **Erro no batch:** para a fila no capítulo com erro (não continua sem o resumo de continuidade)
