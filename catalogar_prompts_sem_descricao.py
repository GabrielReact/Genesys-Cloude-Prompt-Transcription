#!/usr/bin/env python3
"""Cataloga prompts sem descricao e, mediante confirmacao, atualiza descricoes.

O modo padrao e exclusivamente de leitura. A escrita so fica disponivel pelo
botao do HTML servido localmente com ``--servidor`` e exige confirmacao no navegador.
Antes de cada PUT, o prompt e relido: se alguem ja incluiu uma descricao, ele e pulado.
Este programa nunca altera nome, audio, recurso de idioma ou exclui qualquer prompt.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import mimetypes
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from faster_whisper import WhisperModel


PASTA = Path(__file__).resolve().parent
ENV_FILE = PASTA / ".env"
PASTA_AUDIOS = PASTA / "audios_baixados"
ARQUIVO_CSV = PASTA / "prompts_sem_descricao_transcritos.csv"
ARQUIVO_HTML = PASTA / "prompts_sem_descricao_transcritos.html"
CONTEXTO_URA = (
    "Prompt de URA em portugues brasileiro. Use palavras por extenso e escreva "
    "numeros como algarismos. Vocabulario frequente: tecle, digite, protocolo, "
    "atendimento, aguarde para ser atendido, opcao e menu."
)


def ler_env(caminho: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        valores[chave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def normalizar_host(valor: str) -> str:
    """Aceita `api.sae1.pure.cloud` ou `https://api.sae1.pure.cloud`."""
    return valor.strip().removeprefix("https://").removeprefix("http://").rstrip("/")


def host_login(host_api: str) -> str:
    if host_api.startswith("login."):
        return host_api
    if host_api.startswith("api.") or host_api.startswith("apps."):
        return "login." + host_api.split(".", 1)[1]
    return "login." + host_api


def requisitar_json(
    url: str, headers: dict[str, str] | None = None, dados: bytes | None = None, metodo: str | None = None
) -> dict:
    requisicao = Request(url, data=dados, headers=headers or {}, method=metodo)
    with urlopen(requisicao, timeout=60) as resposta:  # nosec B310 - URLs sao Genesys/mediaUri retornada pela Genesys
        return json.loads(resposta.read().decode("utf-8"))


def token_oauth(config: dict[str, str]) -> str:
    host = normalizar_host(config["GENESYS_REGION_HOST"])
    dados = b"grant_type=client_credentials"
    import base64

    credenciais = f'{config["GENESYS_CLIENT_ID"]}:{config["GENESYS_CLIENT_SECRET"]}'.encode()
    headers = {
        "Authorization": "Basic " + base64.b64encode(credenciais).decode(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return requisitar_json(f"https://{host_login(host)}/oauth/token", headers, dados)["access_token"]


def listar_prompts(host: str, token: str) -> list[dict]:
    prompts: list[dict] = []
    pagina = 1
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        retorno = requisitar_json(
            f"https://{host}/api/v2/architect/prompts?pageSize=100&pageNumber={pagina}", headers
        )
        prompts.extend(retorno.get("entities", []))
        if pagina >= retorno.get("pageCount", 1):
            return prompts
        pagina += 1


def extensao_do_audio(uri: str, content_type: str) -> str:
    extensao = Path(urlparse(uri).path).suffix.lower()
    if extensao and len(extensao) <= 5:
        return extensao
    return mimetypes.guess_extension(content_type.split(";", 1)[0]) or ".audio"


def baixar_audio(uri: str, prompt_id: str, idioma: str) -> Path:
    requisicao = Request(uri, headers={"User-Agent": "Prompt-Description-Catalog/1.0"})
    with urlopen(requisicao, timeout=120) as resposta:  # nosec B310 - mediaUri vem da API Genesys
        conteudo = resposta.read()
        extensao = extensao_do_audio(uri, resposta.headers.get("Content-Type", ""))
    PASTA_AUDIOS.mkdir(exist_ok=True)
    destino = PASTA_AUDIOS / f"{prompt_id}_{idioma}{extensao}"
    destino.write_bytes(conteudo)
    return destino


def transcrever(modelo: WhisperModel, arquivo: Path) -> str:
    segmentos, _ = modelo.transcribe(
        str(arquivo), language="pt", vad_filter=True, beam_size=5, initial_prompt=CONTEXTO_URA
    )
    return " ".join(segmento.text.strip() for segmento in segmentos).strip()


def salvar_tabelas(linhas: list[dict[str, str]]) -> None:
    campos = ["nome_prompt", "id_prompt", "idioma", "arquivo_audio", "transcricao", "status"]
    with ARQUIVO_CSV.open("w", newline="", encoding="utf-8-sig") as arquivo:
        gravador = csv.DictWriter(arquivo, fieldnames=campos)
        gravador.writeheader()
        gravador.writerows(linhas)

    cabecalho = "<th><input id='selecionar-todos' type='checkbox' aria-label='Selecionar todos os prompts'></th>" + "".join(
        f"<th>{html.escape(campo)}</th>" for campo in campos
    )
    corpo = "\n".join(
        "<tr><td><input class='selecionar-prompt' type='checkbox' data-id='"
        + html.escape(linha["id_prompt"], quote=True)
        + "' aria-label='Selecionar "
        + html.escape(linha["nome_prompt"], quote=True)
        + "'></td>"
        + "".join(f"<td>{html.escape(linha[campo])}</td>" for campo in campos)
        + "</tr>"
        for linha in linhas
    )
    ARQUIVO_HTML.write_text(
        "<!doctype html><html lang='pt-BR'><meta charset='utf-8'><title>Prompts sem descricao</title>"
        "<style>body{font-family:system-ui;margin:2rem;color:#1f2937}header{display:flex;align-items:center;"
        "justify-content:space-between;gap:1rem;margin-bottom:1.25rem}h1{margin:0}table{border-collapse:collapse;"
        "width:100%}th,td{border:1px solid #ccc;padding:.55rem;text-align:left;vertical-align:top}"
        "th{background:#eee}td:nth-child(6){white-space:pre-wrap}.atualizar{background:#16803c;color:#fff;border:0;"
        "border-radius:.4rem;padding:.75rem 1rem;font-weight:700;cursor:pointer}.atualizar:hover{background:#11632f}"
        ".atualizar:disabled{opacity:.65;cursor:wait}#resultado{white-space:pre-wrap;margin:.8rem 0;color:#173d28}"
        "</style><header>"
        f"<h1>Prompts sem descricao ({len(linhas)})</h1>"
        "<button class='atualizar' id='atualizar' type='button'>Atualizar agora na Org</button></header>"
        "<p id='resultado' aria-live='polite'></p><p id='selecionados'>Nenhum prompt selecionado.</p><table><thead><tr>"
        f"{cabecalho}</tr></thead><tbody>{corpo}</tbody></table>"
        "<script>const b=document.getElementById('atualizar'),r=document.getElementById('resultado'),a=document.getElementById('selecionar-todos'),"
        "q=[...document.querySelectorAll('.selecionar-prompt')],s=document.getElementById('selecionados');"
        "function atualizarSelecao(){const n=q.filter(x=>x.checked).length;a.checked=n===q.length&&n>0;a.indeterminate=n>0&&n<q.length;"
        "b.disabled=n===0;s.textContent=n===1?'1 prompt selecionado.':`${n} prompts selecionados.`}"
        "a.onchange=()=>{q.forEach(x=>x.checked=a.checked);atualizarSelecao()};q.forEach(x=>x.onchange=atualizarSelecao);atualizarSelecao();"
        "b.onclick=async()=>{const ids=q.filter(x=>x.checked).map(x=>x.dataset.id);if(location.protocol==='file:'){r.textContent='Abra esta tabela pelo servidor local: python3 catalogar_prompts_sem_descricao.py --servidor';return;}"
        "if(!confirm(`Atualizar as descricoes dos ${ids.length} prompts selecionados na org? O processo nao altera nome nem audio e pula itens ja preenchidos.`))return;"
        "b.disabled=true;r.textContent='Atualizando...';try{const x=await fetch('/api/atualizar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});"
        "const j=await x.json();if(!x.ok)throw new Error(j.erro||'Falha inesperada');"
        "r.textContent=`Concluido. Atualizados: ${j.atualizados}; pulados: ${j.pulados}; erros: ${j.erros}. `+'A tabela foi atualizada localmente.';"
        "if(j.erros)r.textContent+=' Consulte o campo status.';}catch(e){r.textContent='Erro: '+e.message;}finally{b.disabled=false}};</script></html>",
        encoding="utf-8",
    )


def carregar_linhas() -> list[dict[str, str]]:
    with ARQUIVO_CSV.open(newline="", encoding="utf-8-sig") as arquivo:
        return list(csv.DictReader(arquivo))


def atualizar_descricoes(ids_selecionados: set[str]) -> dict[str, int]:
    """Atualiza somente descricoes vazias, preservando todo o restante do prompt."""
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Configuracao nao encontrada: {ENV_FILE}")
    config = ler_env(ENV_FILE)
    faltando = {"GENESYS_CLIENT_ID", "GENESYS_CLIENT_SECRET", "GENESYS_REGION_HOST"} - config.keys()
    if faltando:
        raise ValueError("Faltam variaveis de configuracao: " + ", ".join(sorted(faltando)))

    token = token_oauth(config)
    host = normalizar_host(config["GENESYS_REGION_HOST"])
    headers = {"Authorization": f"Bearer {token}"}
    resultado = {"atualizados": 0, "pulados": 0, "erros": 0}
    linhas = carregar_linhas()

    for linha in linhas:
        if linha["id_prompt"] not in ids_selecionados:
            continue
        descricao = linha["transcricao"].strip()
        if not descricao:
            linha["status"] = "Pulado: transcricao vazia"
            resultado["pulados"] += 1
            continue
        if linha["status"].startswith("Descricao atualizada") or linha["status"].startswith("Pulado:"):
            resultado["pulados"] += 1
            continue
        try:
            url = f"https://{host}/api/v2/architect/prompts/{linha['id_prompt']}"
            atual = requisitar_json(url, headers)
            if str(atual.get("description") or "").strip():
                linha["status"] = "Pulado: descricao ja preenchida na org"
                resultado["pulados"] += 1
                continue
            corpo = json.dumps({"name": atual["name"], "description": descricao}).encode("utf-8")
            requisitar_json(url, {**headers, "Content-Type": "application/json"}, corpo, metodo="PUT")
            linha["status"] = "Descricao atualizada na org"
            resultado["atualizados"] += 1
        except HTTPError as erro:
            detalhe = erro.read().decode("utf-8", errors="replace").strip()
            linha["status"] = f"Erro ao atualizar: HTTP {erro.code}: {detalhe or erro.reason}"
            resultado["erros"] += 1
        except Exception as erro:
            linha["status"] = f"Erro ao atualizar: {type(erro).__name__}: {erro}"
            resultado["erros"] += 1
        salvar_tabelas(linhas)
    return resultado


def iniciar_servidor(porta: int) -> None:
    """Serve apenas no computador local e disponibiliza a atualizacao confirmada."""
    if not ARQUIVO_HTML.exists() or not ARQUIVO_CSV.exists():
        raise FileNotFoundError("Gere a tabela antes de iniciar o servidor.")

    class Aplicacao(BaseHTTPRequestHandler):
        def responder_json(self, codigo: int, valor: dict) -> None:
            dados = json.dumps(valor, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def do_GET(self) -> None:  # noqa: N802 - exigido por BaseHTTPRequestHandler
            if self.path != "/":
                self.send_error(404)
                return
            dados = ARQUIVO_HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def do_POST(self) -> None:  # noqa: N802 - exigido por BaseHTTPRequestHandler
            if self.path != "/api/atualizar":
                self.responder_json(404, {"erro": "Rota nao encontrada"})
                return
            try:
                tamanho = int(self.headers.get("Content-Length", "0"))
                corpo = json.loads(self.rfile.read(tamanho).decode("utf-8"))
                ids = corpo.get("ids")
                if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
                    self.responder_json(400, {"erro": "Selecione ao menos um prompt valido."})
                    return
                self.responder_json(200, atualizar_descricoes(set(ids)))
            except Exception as erro:
                self.responder_json(500, {"erro": f"{type(erro).__name__}: {erro}"})

        def log_message(self, formato: str, *argumentos: object) -> None:
            print("[servidor] " + formato % argumentos)

    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Aplicacao)
    endereco = f"http://127.0.0.1:{porta}"
    print(f"Tabela disponivel em {endereco} (Ctrl+C para encerrar)")
    threading.Timer(0.3, lambda: webbrowser.open(endereco)).start()
    servidor.serve_forever()


def main() -> int:
    argumentos = argparse.ArgumentParser()
    argumentos.add_argument("--limite", type=int, default=0, help="Limita a quantidade de audios; 0 processa todos.")
    argumentos.add_argument("--somente-listar", action="store_true", help="Gera tabela sem baixar nem transcrever.")
    argumentos.add_argument(
        "--servidor",
        action="store_true",
        help="Abre a tabela no navegador e habilita o botao de atualizacao confirmada.",
    )
    argumentos.add_argument("--porta", type=int, default=8765, help="Porta local do servidor (padrao: 8765).")
    opcoes = argumentos.parse_args()

    if opcoes.servidor:
        iniciar_servidor(opcoes.porta)
        return 0

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Configuracao nao encontrada: {ENV_FILE}")
    config = ler_env(ENV_FILE)
    faltando = {"GENESYS_CLIENT_ID", "GENESYS_CLIENT_SECRET", "GENESYS_REGION_HOST"} - config.keys()
    if faltando:
        raise ValueError("Faltam variaveis de configuracao: " + ", ".join(sorted(faltando)))

    token = token_oauth(config)
    host = normalizar_host(config["GENESYS_REGION_HOST"])
    candidatos: list[tuple[dict, dict]] = []
    for prompt in listar_prompts(host, token):
        if str(prompt.get("description") or "").strip():
            continue
        for recurso in prompt.get("resources", []):
            if recurso.get("mediaUri"):
                candidatos.append((prompt, recurso))

    if opcoes.limite:
        candidatos = candidatos[: opcoes.limite]
    print(f"Prompts de audio sem descricao encontrados: {len(candidatos)}")

    modelo = None if opcoes.somente_listar else WhisperModel("small", device="cpu", compute_type="int8")
    linhas: list[dict[str, str]] = []
    for numero, (prompt, recurso) in enumerate(candidatos, 1):
        nome = str(prompt.get("name") or "(sem nome)")
        prompt_id = str(prompt["id"])
        idioma = str(recurso.get("language") or recurso.get("id") or "desconhecido")
        linha = {"nome_prompt": nome, "id_prompt": prompt_id, "idioma": idioma, "arquivo_audio": "", "transcricao": "", "status": ""}
        try:
            if opcoes.somente_listar:
                linha["status"] = "Pendente de transcricao"
            else:
                arquivo = baixar_audio(str(recurso["mediaUri"]), prompt_id, idioma)
                linha["arquivo_audio"] = arquivo.name
                linha["transcricao"] = transcrever(modelo, arquivo)
                linha["status"] = "Transcrito"
            print(f"[{numero}/{len(candidatos)}] {nome}: {linha['status']}")
        except Exception as erro:  # Mantem a tabela mesmo se algum audio falhar.
            linha["status"] = f"Erro: {type(erro).__name__}: {erro}"
            print(f"[{numero}/{len(candidatos)}] {nome}: {linha['status']}", file=sys.stderr)
        linhas.append(linha)

    salvar_tabelas(linhas)
    print(f"Tabela CSV: {ARQUIVO_CSV}")
    print(f"Tabela HTML: {ARQUIVO_HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
