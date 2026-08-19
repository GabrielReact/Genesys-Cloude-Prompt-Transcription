# Catálogo de descrições de prompts — Genesys Cloud

Ferramenta local em Python para localizar prompts de áudio sem descrição no Genesys Cloud, baixar os áudios, transcrevê-los com Whisper e gerar uma tabela para revisão.

Após revisão, a tabela pode ser aberta em um servidor local e atualizar **somente os prompts selecionados**. Antes de cada atualização, o programa relê o prompt na org e pula o item caso outra pessoa já tenha preenchido a descrição.

## O que a ferramenta altera

- O modo padrão é somente leitura: autentica, lista prompts, baixa áudio e gera CSV/HTML localmente.
- O botão **Atualizar agora na Org** aparece na tabela e exige seleção e confirmação.
- A atualização altera apenas o campo `description` do prompt selecionado.
- Nome, áudio, idioma, recursos e prompts não são alterados ou excluídos.

## Requisitos da máquina

- Linux, macOS ou Windows com **Python 3.10 ou superior**.
- Acesso de rede à região da sua org Genesys Cloud.
- Um navegador para abrir a tabela local.
- Dependência Python: [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper).

Instale a dependência:

```bash
python3 -m pip install --user -r requirements.txt
```

O `faster-whisper` baixa o modelo Whisper na primeira transcrição. Este projeto usa o modelo `small`, em CPU, com `int8`. O pacote usa PyAV, que já inclui as bibliotecas necessárias para leitura de áudio; não é necessário instalar `ffmpeg` separadamente para este script.

## Configuração

Preencha o arquivo `.env` localmente. Nunca suba valores reais de credenciais ao GitHub.

```dotenv
GENESYS_CLIENT_ID=
GENESYS_CLIENT_SECRET=
GENESYS_REGION_HOST=api.sae1.pure.cloud
```

No cliente OAuth da Genesys, atribua a role com estas permissões, no escopo de divisão adequado:

- `Architect > User Prompt > View` — para listar e baixar os prompts.
- `Architect > User Prompt > Edit` — necessária somente para atualizar descrições selecionadas.

## Gerar a tabela

```bash
python3 catalogar_prompts_sem_descricao.py
```

Arquivos locais gerados:

- `prompts_sem_descricao_transcritos.csv`
- `prompts_sem_descricao_transcritos.html`
- `audios_baixados/`

Eles ficam fora do repositório por conterem dados extraídos da org e poderem ocupar bastante espaço.

Para um teste curto, use:

```bash
python3 catalogar_prompts_sem_descricao.py --limite 5
```

## Revisar e atualizar descrições selecionadas

Inicie o servidor local:

```bash
python3 catalogar_prompts_sem_descricao.py --servidor
```

O navegador abre `http://127.0.0.1:8765/`. Marque um ou mais prompts, ou use a caixa do cabeçalho para selecionar todos. Em seguida clique em **Atualizar agora na Org** e confirme.

O servidor escuta apenas em `127.0.0.1`, portanto não fica acessível por outros computadores da rede.

## Observações sobre a transcrição

Whisper é uma ajuda para gerar rascunhos de descrição. Revise principalmente nomes de marcas, siglas, valores, telefones e opções de URA antes de atualizar a org.

O contexto de vocabulário usado na transcrição está na constante `CONTEXTO_URA` do script. Ele melhora termos comuns de URA, mas não garante reconhecimento perfeito de áudios ruins ou nomes próprios.
