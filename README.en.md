# Prompt Description Catalog — Genesys Cloud

[🇧🇷 Português](README.md) | 🇺🇸 English

Local Python tool that finds audio prompts without descriptions in Genesys Cloud, downloads their audio, transcribes them with Whisper, and generates a table for review.

After review, the table can be opened through a local server to update **only the selected prompts**. Before each update, the program reads the prompt from the org again and skips it if someone has already added a description.

## What the tool changes

- The default mode is read-only: it authenticates, lists prompts, downloads audio, and generates local CSV/HTML files.
- The **Update now in the Org** button appears in the table and requires both selection and confirmation.
- An update changes only the selected prompt's `description` field.
- Prompt name, audio, language, resources, and prompts themselves are never changed or deleted.

## Machine requirements

- Linux, macOS, or Windows with **Python 3.10 or later**.
- Network access to your Genesys Cloud org region.
- A browser to open the local table.
- Python dependency: [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper).

Install the dependency:

```bash
python3 -m pip install --user -r requirements.txt
```

`faster-whisper` downloads the Whisper model the first time it runs a transcription. This project uses the `small` model, on CPU, with `int8`. The package uses PyAV, which already includes the libraries required for audio decoding; installing `ffmpeg` separately is not required for this script.

## Configuration

Fill in the local `.env` file. Never push real credential values to GitHub.

```dotenv
GENESYS_CLIENT_ID=
GENESYS_CLIENT_SECRET=
GENESYS_REGION_HOST=api.sae1.pure.cloud
```

In the Genesys OAuth client, assign a role with the following permissions, scoped to the appropriate division:

- `Architect > User Prompt > View` — to list and download prompts.
- `Architect > User Prompt > Edit` — required only to update selected descriptions.

## Generate the table

```bash
python3 catalogar_prompts_sem_descricao.py
```

The following local files are generated:

- `prompts_sem_descricao_transcritos.csv`
- `prompts_sem_descricao_transcritos.html`
- `audios_baixados/`

They are excluded from the repository because they contain data extracted from the org and can take substantial disk space.

For a short test, run:

```bash
python3 catalogar_prompts_sem_descricao.py --limite 5
```

## Review and update selected descriptions

Start the local server:

```bash
python3 catalogar_prompts_sem_descricao.py --servidor
```

Your browser opens `http://127.0.0.1:8765/`. Select one or more prompts, or use the header checkbox to select all of them. Then click **Update now in the Org** and confirm.

The server listens only on `127.0.0.1`, so it is not accessible from other computers on the network.

## Transcription notes

Whisper helps generate description drafts. Review brand names, acronyms, values, phone numbers, and IVR menu options before updating the org.

The transcription vocabulary context is defined in the `CONTEXTO_URA` constant in the script. It improves common IVR terms, but does not guarantee perfect recognition of poor-quality audio or proper names.
