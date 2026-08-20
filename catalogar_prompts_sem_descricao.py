#!/usr/bin/env python3
"""Cataloga prompts sem descrição e, mediante confirmação, atualiza descrições.

O modo padrão é exclusivamente de leitura. A escrita só fica disponível pelo
botão do HTML servido localmente com ``--servidor`` e exige confirmação no navegador.
Antes de cada PUT, o prompt é relido: se alguém já incluiu uma descrição, ele é pulado.
Este programa nunca altera nome, áudio, recurso de idioma ou exclui qualquer prompt.
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
TRAVA_PESQUISA = threading.Lock()
CONTEXTO_URA = (
    "URA em português brasileiro. Preserve nomes próprios, marcas e siglas. "
    "Use algarismos para opções de menu, telefones, CPF, CNPJ, datas, valores e códigos. "
    "Use a palavra hashtag para a tecla #. "
    "Vocabulário frequente: tecle, digite, pressione, opção, menu, menu anterior, "
    "retornar ao início, ouvir novamente, repetir, finalizar, atendimento, "
    "central de relacionamento, aguarde em linha, protocolo, ligação gravada, "
    "transferência, cartão, cartão consignado, segunda via, recarga, saldo, "
    "fatura, cancelamento, senha, documento de identificação, site, login, "
    "WhatsApp e PIX."
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
    with urlopen(requisicao, timeout=60) as resposta:  # nosec B310 - URLs são Genesys/mediaUri retornada pela Genesys
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

    cabecalho = """
        <th class='select-cell'><input id='selecionar-todos' type='checkbox' aria-label='Selecionar todos os prompts visíveis'></th>
        <th class='name-column'>Name <span class='sort-indicator'>◆</span></th>
        <th class='description-column'>Description</th>
        <th class='language-column'>Language</th>
        <th class='status-column'>Status</th>
    """
    corpo = "\n".join(
        "<tr class='prompt-row' data-search='"
        + html.escape(" ".join((linha["nome_prompt"], linha["transcricao"], linha["status"])), quote=True)
        + "'><td class='select-cell'><input class='selecionar-prompt' type='checkbox' data-id='"
        + html.escape(linha["id_prompt"], quote=True)
        + "' aria-label='Selecionar "
        + html.escape(linha["nome_prompt"], quote=True)
        + "'></td><td class='prompt-name' title='"
        + html.escape(linha["id_prompt"], quote=True)
        + "'>"
        + html.escape(linha["nome_prompt"])
        + "</td><td class='prompt-description' title='"
        + html.escape(linha["transcricao"], quote=True)
        + "'>"
        + html.escape(linha["transcricao"] or "—")
        + "</td><td><span class='language-pill'>"
        + html.escape(linha["idioma"])
        + "</span></td><td class='prompt-status' title='"
        + html.escape(linha["status"], quote=True)
        + "'>"
        + html.escape(linha["status"])
        + "</td></tr>"
        for linha in linhas
    )
    pagina = """<!doctype html>
<html lang='pt-BR'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Prompts : User</title>
<style>
  :root { font-family: Arial, Helvetica, sans-serif; color:#283248; background:#fff; font-size:12px; }
  * { box-sizing:border-box; } body { margin:0; min-width:880px; }
  .top-tabs { height:45px; display:flex; align-items:stretch; padding:9px 10px 0; background:#f3f5fa; border-bottom:1px solid #d6dceb; }
  .tab { min-width:122px; padding:10px 12px; color:#536079; background:#eef1f7; font-weight:600; }
  .tab.active { background:#fff; color:#3d4a64; border-bottom:3px solid #365ac5; }
  .command-bar { height:43px; display:flex; align-items:center; gap:7px; padding:7px 19px; border-bottom:1px solid #cfd6e6; background:#fff; }
  button { font:inherit; } .tool-button { height:27px; border:0; border-radius:3px; padding:0 10px; background:#e7ebff; color:#3955aa; font-weight:700; cursor:pointer; }
  .tool-button:hover { background:#dce3ff; } .tool-button:disabled { color:#aeb7d7; cursor:default; }
  .tool-button .icon { font-size:16px; line-height:0; margin-right:5px; vertical-align:-1px; }
  .spacer { flex:1; } .search { display:flex; align-items:center; height:27px; width:241px; border:1px solid #aeb9cf; border-radius:3px; background:#fff; padding:0 8px; }
  .search span { font-size:18px; color:#4e5668; margin-right:7px; } .search input { border:0; outline:0; width:100%; color:#2d374c; font:inherit; }
  .search input::placeholder { color:#7b8498; }.atualizar { background:#16803c; color:#fff; }.atualizar:hover { background:#11632f; }.atualizar:disabled { background:#a5cdb1; color:#fff; }
  .selection-info { min-height:26px; padding:7px 20px 3px; color:#526078; }.selection-info strong { color:#344a96; }.result { display:none; margin:0 20px 8px; padding:7px 10px; border-left:3px solid #16803c; background:#eef9f1; color:#1b5c32; white-space:pre-wrap; }.result.visible { display:block; }.result.error { border-left-color:#b42531; background:#fff0f1; color:#8c1c29; }
  .grid-wrap { margin:0 10px 10px; border:1px solid #cfd6e6; height:calc(100vh - 124px); min-height:440px; overflow:auto; }.grid { width:100%; border-collapse:collapse; table-layout:fixed; }.grid th { position:sticky; top:0; z-index:2; height:32px; padding:0 9px; background:#fff; border-right:1px solid #d4daea; border-bottom:1px solid #cfd6e6; text-align:left; color:#27334a; font-weight:700; }.grid td { height:32px; padding:0 9px; border-right:1px solid #d4daea; border-bottom:1px solid #d6dcea; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }.grid tbody tr:nth-child(even) { background:#e9ecf5; }.grid tbody tr:hover, .grid tbody tr:has(input:checked) { background:#dce5fb; }.grid th:last-child,.grid td:last-child { border-right:0; }.select-cell { width:34px; padding-left:9px!important; text-align:left; }.name-column { width:47%; }.description-column { width:auto; }.language-column { width:66px; text-align:center!important; }.status-column { width:185px; }.prompt-name { color:#3c5dc0; font-weight:600; cursor:default; }.prompt-description { color:#364052; }.prompt-status { font-size:11px; color:#5d687c; }.language-pill { display:inline-block; min-width:38px; padding:3px 7px; border-radius:12px; background:#f1f2f6; color:#5b6477; text-align:center; font-weight:700; font-size:10px; }.sort-indicator { float:right; color:#69748b; font-size:9px; transform:rotate(45deg); }
  input[type='checkbox'] { width:14px; height:14px; accent-color:#3d5fc6; vertical-align:middle; }
</style>
</head>
<body>
  <nav class='top-tabs' aria-label='Navegação'><div class='tab active'>Prompts : User</div></nav>
  <section class='command-bar'>
    <button class='tool-button' id='refresh' type='button'><span class='icon'>⟳</span>Refresh</button>
    <span class='spacer'></span><button class='tool-button atualizar' id='atualizar' type='button'>Atualizar agora na Org</button><label class='search'><span>⌕</span><input id='buscar' type='search' placeholder='Search prompts' aria-label='Buscar prompts'></label>
  </section>
  <p class='selection-info' id='selecionados'>Nenhum prompt selecionado.</p><p class='result' id='resultado' aria-live='polite'></p>
  <main class='grid-wrap'><table class='grid'><thead><tr>__CABECALHO__</tr></thead><tbody>__CORPO__</tbody></table></main>
<script>
const b=document.getElementById('atualizar'),r=document.getElementById('resultado'),a=document.getElementById('selecionar-todos'),q=[...document.querySelectorAll('.selecionar-prompt')],s=document.getElementById('selecionados'),busca=document.getElementById('buscar');
const visiveis=()=>q.filter(x=>!x.closest('tr').hidden);
function atualizarSelecao(){const selecionados=q.filter(x=>x.checked),v=visiveis();a.checked=v.length>0&&v.every(x=>x.checked);a.indeterminate=v.some(x=>x.checked)&&!a.checked;b.disabled=selecionados.length===0;s.innerHTML=selecionados.length===0?'Nenhum prompt selecionado.':`<strong>${selecionados.length}</strong> ${selecionados.length===1?'prompt selecionado.':'prompts selecionados.'}`;}
a.onchange=()=>{visiveis().forEach(x=>x.checked=a.checked);atualizarSelecao()};q.forEach(x=>x.onchange=atualizarSelecao);const refresh=document.getElementById('refresh');refresh.onclick=async()=>{refresh.disabled=true;r.textContent='Pesquisando prompts sem descrição na org...';r.className='result visible';try{const x=await fetch('/api/pesquisar',{method:'POST'}),j=await x.json();if(!x.ok)throw new Error(j.erro||'Falha inesperada');r.textContent=`Pesquisa concluída. ${j.encontrados} prompts sem descrição encontrados; ${j.novos} novos na tabela.`;setTimeout(()=>location.reload(),700);}catch(e){r.textContent='Erro: '+e.message;r.className='result visible error';refresh.disabled=false;}};busca.oninput=()=>{const termo=busca.value.trim().toLocaleLowerCase();document.querySelectorAll('.prompt-row').forEach(l=>l.hidden=termo&&!l.dataset.search.toLocaleLowerCase().includes(termo));atualizarSelecao()};atualizarSelecao();
b.onclick=async()=>{const ids=q.filter(x=>x.checked).map(x=>x.dataset.id);if(location.protocol==='file:'){r.textContent='Abra esta tabela pelo servidor local: python catalogar_prompts_sem_descricao.py --servidor';r.className='result visible error';return;}if(!confirm(`Atualizar as descrições dos ${ids.length} prompts selecionados na org? O processo não altera nome nem áudio e pula itens já preenchidos.`))return;b.disabled=true;r.textContent='Atualizando...';r.className='result visible';try{const x=await fetch('/api/atualizar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});const j=await x.json();if(!x.ok)throw new Error(j.erro||'Falha inesperada');r.textContent=`Concluído. Atualizados: ${j.atualizados}; pulados: ${j.pulados}; erros: ${j.erros}. Recarregue a tabela para ver os status atualizados.`;if(j.erros)r.classList.add('error');}catch(e){r.textContent='Erro: '+e.message;r.className='result visible error';}finally{atualizarSelecao()}};
</script></body></html>"""
    ARQUIVO_HTML.write_text(pagina.replace("__CABECALHO__", cabecalho).replace("__CORPO__", corpo), encoding="utf-8")


def carregar_linhas() -> list[dict[str, str]]:
    with ARQUIVO_CSV.open(newline="", encoding="utf-8-sig") as arquivo:
        return list(csv.DictReader(arquivo))


def pesquisar_prompts_sem_descricao() -> dict[str, int]:
    """Relê a org e atualiza a tabela local, sem alterar nenhum recurso Genesys."""
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Configuração não encontrada: {ENV_FILE}")
    config = ler_env(ENV_FILE)
    faltando = {"GENESYS_CLIENT_ID", "GENESYS_CLIENT_SECRET", "GENESYS_REGION_HOST"} - config.keys()
    if faltando:
        raise ValueError("Faltam variáveis de configuração: " + ", ".join(sorted(faltando)))

    with TRAVA_PESQUISA:
        existentes = {
            (linha["id_prompt"], linha["idioma"]): linha
            for linha in carregar_linhas()
        }
        token = token_oauth(config)
        host = normalizar_host(config["GENESYS_REGION_HOST"])
        novas_linhas: list[dict[str, str]] = []
        novos = 0

        for prompt in listar_prompts(host, token):
            if str(prompt.get("description") or "").strip():
                continue
            for recurso in prompt.get("resources", []):
                if not recurso.get("mediaUri"):
                    continue
                idioma = str(recurso.get("language") or recurso.get("id") or "desconhecido")
                chave = (str(prompt["id"]), idioma)
                linha = existentes.get(chave)
                if linha is None:
                    novos += 1
                    linha = {
                        "nome_prompt": str(prompt.get("name") or "(sem nome)"),
                        "id_prompt": str(prompt["id"]),
                        "idioma": idioma,
                        "arquivo_audio": "",
                        "transcricao": "",
                        "status": "Pendente de transcrição",
                    }
                else:
                    linha["nome_prompt"] = str(prompt.get("name") or "(sem nome)")
                novas_linhas.append(linha)

        salvar_tabelas(novas_linhas)
        return {"encontrados": len(novas_linhas), "novos": novos, "removidos": len(existentes) - len(novas_linhas)}


def atualizar_descricoes(ids_selecionados: set[str]) -> dict[str, int]:
    """Atualiza somente descrições vazias, preservando todo o restante do prompt."""
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Configuração não encontrada: {ENV_FILE}")
    config = ler_env(ENV_FILE)
    faltando = {"GENESYS_CLIENT_ID", "GENESYS_CLIENT_SECRET", "GENESYS_REGION_HOST"} - config.keys()
    if faltando:
        raise ValueError("Faltam variáveis de configuração: " + ", ".join(sorted(faltando)))

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
            linha["status"] = "Pulado: transcrição vazia"
            resultado["pulados"] += 1
            continue
        if linha["status"].startswith("Descrição atualizada") or linha["status"].startswith("Pulado:"):
            resultado["pulados"] += 1
            continue
        try:
            url = f"https://{host}/api/v2/architect/prompts/{linha['id_prompt']}"
            atual = requisitar_json(url, headers)
            if str(atual.get("description") or "").strip():
                linha["status"] = "Pulado: descrição já preenchida na org"
                resultado["pulados"] += 1
                continue
            corpo = json.dumps({"name": atual["name"], "description": descricao}).encode("utf-8")
            requisitar_json(url, {**headers, "Content-Type": "application/json"}, corpo, metodo="PUT")
            linha["status"] = "Descrição atualizada na org"
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
    """Serve apenas no computador local e disponibiliza a atualização confirmada."""
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
            if self.path == "/api/pesquisar":
                try:
                    self.responder_json(200, pesquisar_prompts_sem_descricao())
                except Exception as erro:
                    self.responder_json(500, {"erro": f"{type(erro).__name__}: {erro}"})
                return
            if self.path != "/api/atualizar":
                self.responder_json(404, {"erro": "Rota não encontrada"})
                return
            try:
                tamanho = int(self.headers.get("Content-Length", "0"))
                corpo = json.loads(self.rfile.read(tamanho).decode("utf-8"))
                ids = corpo.get("ids")
                if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
                    self.responder_json(400, {"erro": "Selecione ao menos um prompt válido."})
                    return
                self.responder_json(200, atualizar_descricoes(set(ids)))
            except Exception as erro:
                self.responder_json(500, {"erro": f"{type(erro).__name__}: {erro}"})

        def log_message(self, formato: str, *argumentos: object) -> None:
            print("[servidor] " + formato % argumentos)

    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Aplicacao)
    endereco = f"http://127.0.0.1:{porta}"
    print(f"Tabela disponível em {endereco} (Ctrl+C para encerrar)")
    threading.Timer(0.3, lambda: webbrowser.open(endereco)).start()
    servidor.serve_forever()


def main() -> int:
    argumentos = argparse.ArgumentParser()
    argumentos.add_argument("--limite", type=int, default=0, help="Limita a quantidade de áudios; 0 processa todos.")
    argumentos.add_argument("--somente-listar", action="store_true", help="Gera tabela sem baixar nem transcrever.")
    argumentos.add_argument(
        "--servidor",
        action="store_true",
        help="Abre a tabela no navegador e habilita o botão de atualização confirmada.",
    )
    argumentos.add_argument("--porta", type=int, default=8765, help="Porta local do servidor (padrão: 8765).")
    opcoes = argumentos.parse_args()

    if opcoes.servidor:
        iniciar_servidor(opcoes.porta)
        return 0

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Configuração não encontrada: {ENV_FILE}")
    config = ler_env(ENV_FILE)
    faltando = {"GENESYS_CLIENT_ID", "GENESYS_CLIENT_SECRET", "GENESYS_REGION_HOST"} - config.keys()
    if faltando:
        raise ValueError("Faltam variáveis de configuração: " + ", ".join(sorted(faltando)))

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
    print(f"Prompts de áudio sem descrição encontrados: {len(candidatos)}")

    modelo = None if opcoes.somente_listar else WhisperModel("small", device="cpu", compute_type="int8")
    linhas: list[dict[str, str]] = []
    for numero, (prompt, recurso) in enumerate(candidatos, 1):
        nome = str(prompt.get("name") or "(sem nome)")
        prompt_id = str(prompt["id"])
        idioma = str(recurso.get("language") or recurso.get("id") or "desconhecido")
        linha = {"nome_prompt": nome, "id_prompt": prompt_id, "idioma": idioma, "arquivo_audio": "", "transcricao": "", "status": ""}
        try:
            if opcoes.somente_listar:
                linha["status"] = "Pendente de transcrição"
            else:
                arquivo = baixar_audio(str(recurso["mediaUri"]), prompt_id, idioma)
                linha["arquivo_audio"] = arquivo.name
                linha["transcricao"] = transcrever(modelo, arquivo)
                linha["status"] = "Transcrito"
            print(f"[{numero}/{len(candidatos)}] {nome}: {linha['status']}")
        except Exception as erro:  # Mantém a tabela mesmo se algum áudio falhar.
            linha["status"] = f"Erro: {type(erro).__name__}: {erro}"
            print(f"[{numero}/{len(candidatos)}] {nome}: {linha['status']}", file=sys.stderr)
        linhas.append(linha)

    salvar_tabelas(linhas)
    print(f"Tabela CSV: {ARQUIVO_CSV}")
    print(f"Tabela HTML: {ARQUIVO_HTML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
