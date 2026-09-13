"""
monitor.py — Monitora página e notifica via site local + email
Uso: python monitor.py
"""

import urllib.request
import urllib.parse
import smtplib
import hashlib
import threading
import time
import json
import os
import secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

TZ_BRASILIA = timezone(timedelta(hours=-3))

# ==================== CONFIGURAÇÕES ====================
URL_MONITORADA = "https://www.concursosfcc.com.br/concursos/sface125/index.html"
INTERVALO_SEGUNDOS = 60

EMAIL_DESTINO = "klebercar@gmail.com"
EMAIL_REMETENTE = "klebercar@gmail.com"   # Preencha com seu Gmail
EMAIL_SENHA_APP = "vmkx hziv chuo ktjo"   # Senha de app do Gmail (não a senha normal)

TELEGRAM_TOKEN = "8826569151:AAHA0xAkp10HXKBApPc6Cdp7lU7qP4qaa24"
TELEGRAM_CHAT_ID = "917318112"

PORTA_SERVIDOR = int(os.environ.get("PORT", 5000))

SENHA = "kleberanny"
SESSIONS = set()  # tokens de sessão ativos

PLANILHA_ID = "1_I1TbFTI54YLq1sSpMWQcSGKupaFgbbPPVnh1UZ4Ig4"
PLANILHA_GID = "1992002210"
PLANILHA_NOME = "Kleber Ribeiro Carneiro"
PLANILHA_INSCRICAO = "0020697a"
# =======================================================

# Estado global
estado = {
    "url": URL_MONITORADA,
    "intervalo": INTERVALO_SEGUNDOS,
    "monitorando": False,
    "hash_anterior": None,
    "ultima_verificacao": None,
    "ultima_mudanca": None,
    "total_mudancas": 0,
    "historico": [],
    "status": "parado",
    "email_configurado": bool(EMAIL_REMETENTE and EMAIL_SENHA_APP),
    "proxima_em": 0,
    "heartbeat": None,
    "ultimo_status_http": None,
    "ranking": {
        "monitorando": False,
        "ultima_verificacao": None,
        "ultima_mudanca": None,
        "proxima_em": 0,
        "heartbeat": None,
        "dados": None,
        "dados_anteriores": None,
        "historico": [],
        "total_mudancas": 0,
    },
}


def obter_hash_pagina(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            estado["ultimo_status_http"] = resp.status
            conteudo = resp.read()
            return hashlib.md5(conteudo).hexdigest(), len(conteudo)
    except Exception as e:
        estado["ultimo_status_http"] = "ERRO"
        return None, str(e)


def obter_dados_ranking():
    url = f"https://docs.google.com/spreadsheets/d/{PLANILHA_ID}/export?format=csv&gid={PLANILHA_GID}"
    try:
        import csv, io
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            conteudo = resp.read().decode("utf-8")
        reader = list(csv.reader(io.StringIO(conteudo)))

        todos = []
        meu_dado = None
        cabecalho_ok = False
        for linha in reader:
            if len(linha) > 1 and linha[1] == "Nome":
                cabecalho_ok = True
                continue
            if not cabecalho_ok or not linha[0] or not linha[9]:
                continue
            try:
                total = float(linha[9].replace(",", "."))
                todos.append((total, linha[0]))
                if PLANILHA_INSCRICAO in linha[0]:
                    meu_dado = linha
            except:
                pass

        if meu_dado is None:
            return None, "Candidato não encontrado na planilha"

        todos.sort(key=lambda x: -x[0])
        pos_recalculada = next((i + 1 for i, (_, insc) in enumerate(todos) if PLANILHA_INSCRICAO in insc), "-")

        com_titulos = sum(1 for linha in reader[5:] if len(linha) > 8 and linha[8] and linha[8] not in ("0,00", "0.00", "-", ""))

        minha_obj_disc = float(meu_dado[6].replace(",", ".")) + float(meu_dado[7].replace(",", "."))
        meu_total = float(meu_dado[9].replace(",", "."))
        limite_inferior = meu_total - 11
        ameacas = []
        for linha in reader[5:]:
            if len(linha) > 9 and linha[0] and linha[9] and PLANILHA_INSCRICAO not in linha[0]:
                try:
                    total = float(linha[9].replace(",", "."))
                    tit = linha[8].strip()
                    sem_titulo = not tit or tit in ("0,00", "0.00", "-")
                    if total < meu_total and total >= limite_inferior and sem_titulo:
                        obj_disc = float(linha[6].replace(",", ".")) + float(linha[7].replace(",", "."))
                        ameacas.append({"nome": linha[1], "total": linha[9], "obj_disc": round(obj_disc, 2), "diferenca": round(meu_total - total, 2)})
                except:
                    pass
        ameacas.sort(key=lambda x: -x["obj_disc"])

        return {
            "inscricao": meu_dado[0],
            "nome": meu_dado[1],
            "listas": meu_dado[2],
            "class_geral": meu_dado[3],
            "nota_obj": meu_dado[6],
            "nota_disc": meu_dado[7],
            "titulacao": meu_dado[8],
            "total_pontos": meu_dado[9],
            "vai_assumir": meu_dado[11],
            "situacao": meu_dado[16],
            "elegivel": meu_dado[21],
            "class_liq_geral": meu_dado[22],
            "proximo_geral": meu_dado[25] if len(meu_dado) > 25 else "-",
            "pos_recalculada": str(pos_recalculada),
            "total_candidatos": str(len(todos)),
            "com_titulos": str(com_titulos),
            "ameacas": ameacas,
        }, None
    except Exception as e:
        return None, str(e)


def loop_ranking():
    est = estado["ranking"]
    print(f"[RANKING] Iniciando monitoramento da planilha")
    while est["monitorando"]:
        est["heartbeat"] = time.time()
        agora = datetime.now(TZ_BRASILIA).strftime("%d/%m/%Y %H:%M:%S")
        dados, erro = obter_dados_ranking()
        est["ultima_verificacao"] = agora

        if dados is None:
            est["historico"].insert(0, {"hora": agora, "tipo": "erro", "msg": erro})
        elif est["dados_anteriores"] is None:
            est["dados"] = dados
            est["dados_anteriores"] = dados
            est["historico"].insert(0, {"hora": agora, "tipo": "inicio", "msg": "Monitoramento iniciado"})
        else:
            ant = est["dados_anteriores"]
            mudancas = []
            if dados["pos_recalculada"] != ant["pos_recalculada"]:
                mudancas.append(f"Posição Recalculada: {ant['pos_recalculada']}º → {dados['pos_recalculada']}º")
            if dados["class_liq_geral"] != ant["class_liq_geral"]:
                mudancas.append(f"Class. Líquida Geral: {ant['class_liq_geral']} → {dados['class_liq_geral']}")
            if dados["situacao"] != ant["situacao"]:
                mudancas.append(f"Situação: {ant['situacao']} → {dados['situacao']}")
            if dados["proximo_geral"] != ant["proximo_geral"]:
                mudancas.append(f"Próximo Geral: {ant['proximo_geral']} → {dados['proximo_geral']}")

            est["dados"] = dados
            if mudancas:
                est["dados_anteriores"] = dados
                est["ultima_mudanca"] = agora
                est["total_mudancas"] += 1
                msg = " | ".join(mudancas)
                est["historico"].insert(0, {"hora": agora, "tipo": "mudanca", "msg": msg})
                enviar_telegram(
                    f"📊 Ranking Atualizado!\n{PLANILHA_NOME}\n" +
                    "\n".join(mudancas) +
                    f"\nDetectado em: {agora}"
                )
            else:
                est["historico"].insert(0, {"hora": agora, "tipo": "ok", "msg": "Sem mudanças no ranking"})

        est["historico"] = est["historico"][:50]

        for i in range(estado["intervalo"], 0, -1):
            if not est["monitorando"]:
                return
            est["proxima_em"] = i
            est["heartbeat"] = time.time()
            time.sleep(1)
        est["proxima_em"] = 0


def enviar_telegram(mensagem):
    try:
        dados = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=dados)
        urllib.request.urlopen(req, timeout=10)
        print("[TELEGRAM] Mensagem enviada!")
        return True
    except Exception as e:
        print(f"[TELEGRAM] Erro: {e}")
        return False


def enviar_email(assunto, corpo):
    if not EMAIL_REMETENTE or not EMAIL_SENHA_APP:
        print("[EMAIL] Não configurado, pulando envio.")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"] = EMAIL_REMETENTE
        msg["To"] = EMAIL_DESTINO
        msg.attach(MIMEText(corpo, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_REMETENTE, EMAIL_SENHA_APP)
            server.sendmail(EMAIL_REMETENTE, EMAIL_DESTINO, msg.as_string())
        print(f"[EMAIL] Enviado para {EMAIL_DESTINO}")
        return True
    except Exception as e:
        print(f"[EMAIL] Erro: {e}")
        return False


def loop_autoping():
    time.sleep(60)
    while True:
        try:
            urllib.request.urlopen("https://monitor-fcc.onrender.com/login", timeout=10)
            print("[AUTOPING] OK")
        except:
            pass
        time.sleep(840)  # ping a cada 14 minutos


def loop_monitoramento():
    print(f"[MONITOR] Iniciando monitoramento de: {estado['url']}")
    while estado["monitorando"]:
        estado["heartbeat"] = time.time()
        agora = datetime.now(TZ_BRASILIA).strftime("%d/%m/%Y %H:%M:%S")
        hash_atual, info = obter_hash_pagina(estado["url"])
        estado["ultima_verificacao"] = agora

        if hash_atual is None:
            print(f"[{agora}] ERRO ao acessar página: {info}")
            estado["historico"].insert(0, {"hora": agora, "tipo": "erro", "msg": str(info)})
        elif estado["hash_anterior"] is None:
            estado["hash_anterior"] = hash_atual
            print(f"[{agora}] Hash inicial capturado: {hash_atual}")
            estado["historico"].insert(0, {"hora": agora, "tipo": "inicio", "msg": "Monitoramento iniciado"})
        elif hash_atual != estado["hash_anterior"]:
            print(f"[{agora}] ⚡ MUDANÇA DETECTADA!")
            estado["hash_anterior"] = hash_atual
            estado["ultima_mudanca"] = agora
            estado["total_mudancas"] += 1
            estado["historico"].insert(0, {"hora": agora, "tipo": "mudanca", "msg": "Página atualizada!"})

            corpo_email = f"""
            <h2>⚡ Página Atualizada!</h2>
            <p><strong>URL:</strong> <a href="{estado['url']}">{estado['url']}</a></p>
            <p><strong>Detectado em:</strong> {agora}</p>
            <p><strong>Total de mudanças:</strong> {estado['total_mudancas']}</p>
            <br>
            <p>Acesse o <a href="http://localhost:{PORTA_SERVIDOR}">painel de monitoramento</a> para mais detalhes.</p>
            """
            enviar_email("⚡ Página do Concurso FCC Atualizada!", corpo_email)
            enviar_telegram(f"⚡ Página do Concurso FCC Atualizada!\nDetectado em: {agora}\nTotal de mudanças: {estado['total_mudancas']}\n{estado['url']}")
        else:
            print(f"[{agora}] Sem mudanças.")
            estado["historico"].insert(0, {"hora": agora, "tipo": "ok", "msg": "Sem mudanças"})

        # Mantém apenas os últimos 50 registros
        estado["historico"] = estado["historico"][:50]

        for i in range(estado["intervalo"], 0, -1):
            if not estado["monitorando"]:
                return
            estado["proxima_em"] = i
            estado["heartbeat"] = time.time()
            time.sleep(1)
        estado["proxima_em"] = 0


HTML_LOGIN = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login — Monitor FCC</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .box { background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 40px; width: 100%; max-width: 360px; }
  h1 { font-size: 20px; margin-bottom: 8px; }
  p { font-size: 13px; color: #64748b; margin-bottom: 24px; }
  input { width: 100%; padding: 12px; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; font-size: 14px; margin-bottom: 16px; }
  button { width: 100%; padding: 12px; border-radius: 8px; border: none; background: #3b82f6; color: white; font-size: 14px; font-weight: 600; cursor: pointer; }
  button:hover { background: #2563eb; }
  .erro { color: #ef4444; font-size: 13px; margin-bottom: 12px; display: none; }
  .erro.visivel { display: block; }
</style>
</head>
<body>
<div class="box">
  <h1>🔍 Monitor FCC</h1>
  <p>Digite a senha para acessar o painel.</p>
  <div class="erro" id="erro">Senha incorreta.</div>
  <input type="password" id="senha" placeholder="Senha" onkeydown="if(event.key==='Enter') entrar()">
  <button onclick="entrar()">Entrar</button>
</div>
<script>
async function entrar() {
  const senha = document.getElementById('senha').value;
  const r = await fetch('/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ senha }) });
  const d = await r.json();
  if (d.ok) window.location.href = '/';
  else document.getElementById('erro').classList.add('visivel');
}
</script>
</body>
</html>"""

HTML_RANKING = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ranking — FCC TI</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .header { background: #1e293b; padding: 20px 32px; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 20px; font-weight: 700; }
  .nav { background: #1e293b; border-bottom: 1px solid #334155; display: flex; gap: 0; }
  .nav a { padding: 12px 24px; font-size: 13px; font-weight: 600; color: #94a3b8; text-decoration: none; border-bottom: 2px solid transparent; }
  .nav a.ativo { color: #e2e8f0; border-bottom-color: #3b82f6; }
  .nav { align-items: center; }
  .container { max-width: 900px; margin: 32px auto; padding: 0 20px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
  .card .label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }
  .card .value { font-size: 22px; font-weight: 700; }
  .card.verde .value { color: #10b981; }
  .card.vermelho .value { color: #ef4444; }
  .card.azul .value { color: #3b82f6; }
  .card.amarelo .value { color: #f59e0b; }
  .card.roxo .value { color: #a855f7; }
  .painel { background: #1e293b; border-radius: 12px; border: 1px solid #334155; padding: 24px; margin-bottom: 24px; }
  .painel h2 { font-size: 14px; color: #94a3b8; margin-bottom: 16px; }
  .linha { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #334155; font-size: 13px; }
  .linha:last-child { border-bottom: none; }
  .linha .chave { color: #64748b; }
  .linha .valor { font-weight: 600; }
  .controles { display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }
  .btn { padding: 10px 20px; border-radius: 8px; border: none; font-size: 13px; font-weight: 600; cursor: pointer; transition: .2s; }
  .btn-verde { background: #10b981; color: white; } .btn-verde:hover { background: #059669; }
  .btn-vermelho { background: #ef4444; color: white; } .btn-vermelho:hover { background: #dc2626; }
  .btn-azul { background: #3b82f6; color: white; } .btn-azul:hover { background: #2563eb; }
  .historico { background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; }
  .historico h2 { padding: 16px 20px; font-size: 14px; border-bottom: 1px solid #334155; color: #94a3b8; }
  .item { display: flex; align-items: center; gap: 12px; padding: 12px 20px; border-bottom: 1px solid #0f172a; font-size: 13px; }
  .item:last-child { border-bottom: none; }
  .item .hora { color: #64748b; min-width: 140px; font-size: 12px; }
  .badge { padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; }
  .badge-mudanca { background: #fef3c7; color: #92400e; }
  .badge-ok { background: #d1fae5; color: #065f46; }
  .badge-erro { background: #fee2e2; color: #991b1b; }
  .badge-inicio { background: #dbeafe; color: #1e40af; }
  .countdown { background: #1e293b; border-radius: 12px; padding: 16px 20px; margin-bottom: 24px; border: 1px solid #334155; font-size: 13px; color: #94a3b8; display: none; }
  .alerta { background: #422006; border: 1px solid #f59e0b; border-radius: 12px; padding: 16px 20px; margin-bottom: 24px; font-size: 13px; color: #fcd34d; display: none; }
  .alerta.visivel { display: block; }
  .destaque { background: #1e3a5f; border: 1px solid #3b82f6; border-radius: 8px; padding: 4px 10px; color: #93c5fd; font-weight: 700; }
</style>
</head>
<body>
<div class="header"><span>📊</span><h1>Monitor FCC — Auditor Fiscal TI</h1></div>
<nav class="nav">
  <a href="/">🔍 Página FCC</a>
  <a href="/ranking" class="ativo">📊 Meu Ranking</a>
  <button onclick="iniciarTudo()" style="margin-left:12px;padding:6px 16px;border-radius:8px;border:none;background:#10b981;color:white;font-size:13px;font-weight:600;cursor:pointer;">▶▶ Iniciar Tudo</button>
</nav>
<div class="container">
  <div class="alerta" id="alerta">📊 <strong>Ranking atualizado!</strong> Sua posição mudou.</div>

  <div class="cards" id="cards-ranking">
    <div class="card azul"><div class="label">Class. Original Lista Geral</div><div class="value" id="r-class-orig">—</div></div>
    <div class="card verde"><div class="label">Class. Líquida Lista Geral</div><div class="value" id="r-class-liq">—</div></div>
    <div class="card roxo"><div class="label">Posição Recalculada (c/ títulos)</div><div class="value" id="r-pos-recalc">—</div></div>
    <div class="card amarelo"><div class="label">Total de Pontos</div><div class="value" id="r-pontos">—</div></div>
    <div class="card azul"><div class="label">Títulos Preenchidos</div><div class="value" id="r-com-titulos">—</div></div>
    <div class="card vermelho"><div class="label">Ameaças (sem título, ≤11pts)</div><div class="value" id="r-ameacas">—</div></div>
    <div class="card" id="card-situacao"><div class="label">Situação</div><div class="value" style="font-size:14px" id="r-situacao">—</div></div>
    <div class="card" id="card-hb"><div class="label">Heartbeat</div><div class="value" style="font-size:13px" id="r-hb">—</div></div>
  </div>

  <div class="painel" id="painel-detalhes" style="display:none">
    <h2>📋 Detalhes — __NOME__</h2>
    <div class="linha"><span class="chave">Posição recalculada (c/ títulos)</span><span class="valor" id="d-pos-recalc">—</span></div>
    <div class="linha"><span class="chave">Total de candidatos</span><span class="valor" id="d-total-cand">—</span></div>
    <div class="linha"><span class="chave">Inscrição</span><span class="valor" id="d-insc">—</span></div>
    <div class="linha"><span class="chave">Listas</span><span class="valor" id="d-listas">—</span></div>
    <div class="linha"><span class="chave">Nota Objetiva</span><span class="valor" id="d-obj">—</span></div>
    <div class="linha"><span class="chave">Nota Discursiva</span><span class="valor" id="d-disc">—</span></div>
    <div class="linha"><span class="chave">Titulação</span><span class="valor" id="d-tit">—</span></div>
    <div class="linha"><span class="chave">Vai Assumir?</span><span class="valor" id="d-assumir">—</span></div>
    <div class="linha"><span class="chave">Elegível próxima chamada?</span><span class="valor" id="d-eleg">—</span></div>
    <div class="linha"><span class="chave">Próximo Lista Geral</span><span class="valor" id="d-prox-geral">—</span></div>
    <div class="linha"><span class="chave">Última verificação</span><span class="valor" id="d-verif">—</span></div>
    <div class="linha"><span class="chave">Última mudança</span><span class="valor" id="d-mudanca">—</span></div>
  </div>

  <div class="painel" id="painel-ameacas" style="display:none">
    <h2>⚠️ Ameaças — Sem título, total entre 276,10 e 287,10 (até 11pts abaixo de você)</h2>
    <div id="ameacas-lista"></div>
  </div>

  <div class="controles">
    <button class="btn btn-verde" onclick="iniciarRanking()">▶ Iniciar</button>
    <button class="btn btn-vermelho" onclick="pararRanking()">⏹ Parar</button>
    <button class="btn btn-azul" onclick="verificarRanking()">🔄 Verificar Agora</button>
    <button class="btn" style="background:#475569" onclick="limparHistoricoRanking()">🗑 Limpar Histórico</button>
  </div>

  <div class="countdown" id="countdown-box">⏱ Próxima verificação em: <strong id="countdown-val">—</strong></div>

  <div class="historico">
    <h2>📋 Histórico de Mudanças</h2>
    <div id="historico-lista"></div>
  </div>
</div>
<script>
let ultimaMudanca = null;
let proximaVerificacao = null;

async function iniciarTudo() {
  await fetch('/api/iniciar-tudo', { method: 'POST' });
  atualizar();
}

async function iniciarRanking() { await fetch('/api/ranking/iniciar', { method: 'POST' }); atualizar(); }
async function pararRanking() { await fetch('/api/ranking/parar', { method: 'POST' }); atualizar(); }
async function verificarRanking() { await fetch('/api/ranking/verificar', { method: 'POST' }); setTimeout(atualizar, 3000); }
async function limparHistoricoRanking() { await fetch('/api/ranking/limpar-historico', { method: 'POST' }); atualizar(); }

async function atualizar() {
  const r = await fetch('/api/ranking/estado');
  const d = await r.json();

  document.getElementById('r-class-orig').textContent = d.dados?.class_geral || '—';
  document.getElementById('r-class-liq').textContent = d.dados?.class_liq_geral || '—';
  document.getElementById('r-pos-recalc').textContent = d.dados ? (d.dados.pos_recalculada + 'º / ' + d.dados.total_candidatos) : '—';
  document.getElementById('r-pontos').textContent = d.dados?.total_pontos || '—';

  const sit = d.dados?.situacao || '—';
  document.getElementById('r-situacao').textContent = sit;
  document.getElementById('card-situacao').className = 'card ' + (sit === 'Aguardando' ? 'verde' : sit === '—' ? '' : 'amarelo');

  if (d.dados) {
    document.getElementById('painel-detalhes').style.display = 'block';
    document.getElementById('d-pos-recalc').textContent = d.dados.pos_recalculada + 'º de ' + d.dados.total_candidatos;
    document.getElementById('d-total-cand').textContent = d.dados.total_candidatos;
    document.getElementById('r-com-titulos').textContent = d.dados.com_titulos + ' / ' + d.dados.total_candidatos;

    const am = d.dados.ameacas || [];
    document.getElementById('r-ameacas').textContent = am.length;
    const painelAm = document.getElementById('painel-ameacas');
    painelAm.style.display = am.length ? 'block' : 'none';
    document.getElementById('ameacas-lista').innerHTML = am.length
      ? `<table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="color:#64748b;font-size:11px;text-transform:uppercase">
            <th style="text-align:left;padding:8px 0">Nome</th>
            <th style="text-align:right;padding:8px">Obj+Disc</th>
            <th style="text-align:right;padding:8px">Distância</th>
            <th style="text-align:right;padding:8px">Total atual</th>
          </tr>
          ${am.map(a => `<tr style="border-top:1px solid #334155">
            <td style="padding:8px 0">${a.nome}</td>
            <td style="text-align:right;padding:8px;color:#f59e0b">${a.obj_disc}</td>
            <td style="text-align:right;padding:8px;color:#ef4444">-${a.diferenca}</td>
            <td style="text-align:right;padding:8px;color:#94a3b8">${a.total}</td>
          </tr>`).join('')}
        </table>`
      : '<div style="color:#64748b;padding:8px 0">Nenhuma ameaça no momento.</div>';
    document.getElementById('d-insc').textContent = d.dados.inscricao;
    document.getElementById('d-listas').textContent = d.dados.listas;
    document.getElementById('d-obj').textContent = d.dados.nota_obj;
    document.getElementById('d-disc').textContent = d.dados.nota_disc;
    document.getElementById('d-tit').textContent = d.dados.titulacao;
    document.getElementById('d-assumir').textContent = d.dados.vai_assumir;
    document.getElementById('d-eleg').textContent = d.dados.elegivel;
    document.getElementById('d-prox-geral').innerHTML = d.dados.proximo_geral ? `<span class="destaque">${d.dados.proximo_geral}</span>` : '—';
  }

  document.getElementById('d-verif').textContent = d.ultima_verificacao || '—';
  document.getElementById('d-mudanca').textContent = d.ultima_mudanca || 'Nenhuma';

  const hb = d.heartbeat;
  const cardHb = document.getElementById('card-hb');
  if (hb) {
    const seg = Math.round(Date.now() / 1000 - hb);
    document.getElementById('r-hb').textContent = seg + 's atrás';
    cardHb.className = 'card ' + (seg < 10 ? 'verde' : seg < 30 ? 'amarelo' : 'vermelho');
  }

  if (d.monitorando) {
    proximaVerificacao = Date.now() + d.proxima_em * 1000;
    document.getElementById('countdown-box').style.display = 'block';
  } else {
    proximaVerificacao = null;
    document.getElementById('countdown-box').style.display = 'none';
  }

  if (d.ultima_mudanca && d.ultima_mudanca !== ultimaMudanca) {
    ultimaMudanca = d.ultima_mudanca;
    document.getElementById('alerta').classList.add('visivel');
    setTimeout(() => document.getElementById('alerta').classList.remove('visivel'), 10000);
  }

  document.getElementById('historico-lista').innerHTML = d.historico.map(h => `
    <div class="item">
      <span class="hora">${h.hora}</span>
      <span class="badge badge-${h.tipo}">${h.tipo === 'mudanca' ? '📊 MUDANÇA' : h.tipo === 'ok' ? '✓ OK' : h.tipo === 'erro' ? '✗ ERRO' : 'ℹ INÍCIO'}</span>
      <span>${h.msg}</span>
    </div>
  `).join('') || '<div class="item" style="color:#64748b">Nenhum registro ainda.</div>';
}

setInterval(() => {
  if (proximaVerificacao) {
    const seg = Math.max(0, Math.round((proximaVerificacao - Date.now()) / 1000));
    document.getElementById('countdown-val').textContent = seg + 's';
  }
}, 1000);

atualizar();
setInterval(atualizar, 5000);
</script>
</body>
</html>
""".replace("__NOME__", PLANILHA_NOME)

HTML_PAGINA = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Monitor de Página</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .header { background: #1e293b; padding: 20px 32px; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 20px; font-weight: 700; }
  .header span { font-size: 24px; }
  .container { max-width: 900px; margin: 32px auto; padding: 0 20px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
  .card .label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }
  .card .value { font-size: 22px; font-weight: 700; }
  .card.verde .value { color: #10b981; }
  .card.vermelho .value { color: #ef4444; }
  .card.azul .value { color: #3b82f6; }
  .card.amarelo .value { color: #f59e0b; }
  .url-box { background: #1e293b; border-radius: 12px; padding: 16px 20px; margin-bottom: 24px; border: 1px solid #334155; font-size: 13px; color: #94a3b8; word-break: break-all; }
  .url-box a { color: #3b82f6; }
  .controles { display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; align-items: center; }
  .btn { padding: 10px 20px; border-radius: 8px; border: none; font-size: 13px; font-weight: 600; cursor: pointer; transition: .2s; }
  .btn-verde { background: #10b981; color: white; }
  .btn-verde:hover { background: #059669; }
  .btn-vermelho { background: #ef4444; color: white; }
  .btn-vermelho:hover { background: #dc2626; }
  .btn-azul { background: #3b82f6; color: white; }
  .btn-azul:hover { background: #2563eb; }
  .intervalo { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #94a3b8; }
  .intervalo input { width: 70px; padding: 8px; border-radius: 6px; border: 1px solid #334155; background: #0f172a; color: #e2e8f0; font-size: 13px; text-align: center; }
  .historico { background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; }
  .historico h2 { padding: 16px 20px; font-size: 14px; border-bottom: 1px solid #334155; color: #94a3b8; }
  .item { display: flex; align-items: center; gap: 12px; padding: 12px 20px; border-bottom: 1px solid #1e293b; font-size: 13px; }
  .item:last-child { border-bottom: none; }
  .item .hora { color: #64748b; min-width: 140px; font-size: 12px; }
  .badge { padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; }
  .badge-mudanca { background: #fef3c7; color: #92400e; }
  .badge-ok { background: #d1fae5; color: #065f46; }
  .badge-erro { background: #fee2e2; color: #991b1b; }
  .badge-inicio { background: #dbeafe; color: #1e40af; }
  .alerta { background: #422006; border: 1px solid #f59e0b; border-radius: 12px; padding: 16px 20px; margin-bottom: 24px; font-size: 13px; color: #fcd34d; display: none; }
  .alerta.visivel { display: block; }
</style>
</head>
<body>
<div class="header">
  <span>🔍</span>
  <h1>Monitor de Página — FCC Concursos</h1>
</div>
<nav style="background:#1e293b;border-bottom:1px solid #334155;display:flex;align-items:center;">
  <a href="/" style="padding:12px 24px;font-size:13px;font-weight:600;color:#e2e8f0;text-decoration:none;border-bottom:2px solid #3b82f6;">🔍 Página FCC</a>
  <a href="/ranking" style="padding:12px 24px;font-size:13px;font-weight:600;color:#94a3b8;text-decoration:none;border-bottom:2px solid transparent;">📊 Meu Ranking</a>
  <button onclick="iniciarTudo()" style="margin-left:12px;padding:6px 16px;border-radius:8px;border:none;background:#10b981;color:white;font-size:13px;font-weight:600;cursor:pointer;">▶▶ Iniciar Tudo</button>
</nav>
<div class="container">
  <div class="alerta" id="alerta">⚡ <strong>Mudança detectada!</strong> A página foi atualizada.</div>

  <div class="url-box">
    🔗 <a href="__URL__" target="_blank">__URL__</a>
  </div>

  <div class="cards">
    <div class="card" id="card-status">
      <div class="label">Status</div>
      <div class="value" id="status-val">—</div>
    </div>
    <div class="card amarelo">
      <div class="label">Total de Mudanças</div>
      <div class="value" id="mudancas-val">0</div>
    </div>
    <div class="card azul">
      <div class="label">Última Verificação</div>
      <div class="value" style="font-size:13px;padding-top:4px;" id="verificacao-val">—</div>
    </div>
    <div class="card verde">
      <div class="label">Última Mudança</div>
      <div class="value" style="font-size:13px;padding-top:4px;" id="mudanca-val">—</div>
    </div>
    <div class="card" id="card-http">
      <div class="label">Status HTTP</div>
      <div class="value" id="http-val">—</div>
    </div>
    <div class="card" id="card-heartbeat">
      <div class="label">Heartbeat</div>
      <div class="value" style="font-size:13px;padding-top:4px;" id="heartbeat-val">—</div>
    </div>
  </div>

  <div class="controles">
    <button class="btn btn-verde" onclick="iniciar()">▶ Iniciar</button>
    <button class="btn btn-vermelho" onclick="parar()">⏹ Parar</button>
    <button class="btn btn-azul" onclick="verificarAgora()">🔄 Verificar Agora</button>
    <button class="btn" style="background:#475569" onclick="limparHistorico()">🗑 Limpar Histórico</button>
    <button class="btn" style="background:#7c3aed" onclick="testarEmail()">📧 Testar Email</button>
    <button class="btn" style="background:#0088cc" onclick="testarTelegram()">✈️ Testar Telegram</button>
    <div class="intervalo">
      Intervalo:
      <input type="number" id="intervalo-input" value="60" min="10">
      segundos
      <button class="btn btn-azul" onclick="salvarIntervalo()" style="padding:8px 12px;">Salvar</button>
    </div>
  </div>

  <div style="background:#1e293b;border-radius:12px;padding:16px 20px;margin-bottom:24px;border:1px solid #334155;font-size:13px;color:#94a3b8;" id="countdown-box" style="display:none">
    ⏱ Próxima verificação em: <strong id="countdown-val">—</strong>
  </div>

  <div class="historico">
    <h2>📋 Histórico</h2>
    <div id="historico-lista"></div>
  </div>
</div>

<script>
let ultimaMudanca = null;

async function iniciar() {
  await fetch('/api/iniciar', { method: 'POST' });
  atualizar();
}

async function parar() {
  await fetch('/api/parar', { method: 'POST' });
  atualizar();
}

async function verificarAgora() {
  await fetch('/api/verificar', { method: 'POST' });
  setTimeout(atualizar, 2000);
}

async function salvarIntervalo() {
  const v = document.getElementById('intervalo-input').value;
  await fetch('/api/intervalo', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ intervalo: parseInt(v) }) });
}

async function testarEmail() {
  const r = await fetch('/api/testar-email', { method: 'POST' });
  const d = await r.json();
  alert(d.ok ? '✅ Email de teste enviado com sucesso!' : '❌ Erro ao enviar email: ' + d.erro);
}

async function testarTelegram() {
  const r = await fetch('/api/testar-telegram', { method: 'POST' });
  const d = await r.json();
  alert(d.ok ? '✅ Mensagem enviada no Telegram!' : '❌ Erro no Telegram: ' + d.erro);
}

let proximaVerificacao = null;

async function atualizar() {
  const r = await fetch('/api/estado');
  const d = await r.json();

  document.getElementById('status-val').textContent = d.monitorando ? '🟢 Ativo' : '🔴 Parado';
  document.getElementById('mudancas-val').textContent = d.total_mudancas;
  document.getElementById('verificacao-val').textContent = d.ultima_verificacao || '—';
  document.getElementById('mudanca-val').textContent = d.ultima_mudanca || 'Nenhuma';
  document.getElementById('intervalo-input').value = d.intervalo;

  const http = d.ultimo_status_http;
  const cardHttp = document.getElementById('card-http');
  const httpVal = document.getElementById('http-val');
  httpVal.textContent = http || '—';
  cardHttp.className = 'card ' + (http === 200 ? 'verde' : http ? 'vermelho' : '');

  const hb = d.heartbeat;
  const cardHb = document.getElementById('card-heartbeat');
  const hbVal = document.getElementById('heartbeat-val');
  if (hb) {
    const seg = Math.round(Date.now() / 1000 - hb);
    hbVal.textContent = seg + 's atrás';
    cardHb.className = 'card ' + (seg < 10 ? 'verde' : seg < 30 ? 'amarelo' : 'vermelho');
  } else {
    hbVal.textContent = '—';
    cardHb.className = 'card';
  }

  if (d.monitorando) {
    proximaVerificacao = Date.now() + d.proxima_em * 1000;
    document.getElementById('countdown-box').style.display = 'block';
  } else {
    proximaVerificacao = null;
    document.getElementById('countdown-box').style.display = 'none';
    document.getElementById('countdown-val').textContent = '—';
  }

  if (d.ultima_mudanca && d.ultima_mudanca !== ultimaMudanca) {
    ultimaMudanca = d.ultima_mudanca;
    document.getElementById('alerta').classList.add('visivel');
    setTimeout(() => document.getElementById('alerta').classList.remove('visivel'), 10000);
  }

  const lista = document.getElementById('historico-lista');
  lista.innerHTML = d.historico.map(h => `
    <div class="item">
      <span class="hora">${h.hora}</span>
      <span class="badge badge-${h.tipo}">${h.tipo === 'mudanca' ? '⚡ MUDANÇA' : h.tipo === 'ok' ? '✓ OK' : h.tipo === 'erro' ? '✗ ERRO' : 'ℹ INÍCIO'}</span>
      <span>${h.msg}</span>
    </div>
  `).join('') || '<div class="item" style="color:#64748b">Nenhum registro ainda.</div>';
}

setInterval(() => {
  if (proximaVerificacao) {
    const seg = Math.max(0, Math.round((proximaVerificacao - Date.now()) / 1000));
    document.getElementById('countdown-val').textContent = seg + 's';
  }
}, 1000);

async function iniciarTudo() {
  await fetch('/api/iniciar-tudo', { method: 'POST' });
  atualizar();
}

async function limparHistorico() {
  await fetch('/api/limpar-historico', { method: 'POST' });
  atualizar();
}

atualizar();
setInterval(atualizar, 5000);
</script>
</body>
</html>
""".replace("__URL__", URL_MONITORADA)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _autenticado(self):
        cookie = self.headers.get("Cookie", "")
        for parte in cookie.split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "session" and v in SESSIONS:
                return True
        return False

    def _redirecionar_login(self):
        self.send_response(302)
        self.send_header("Location", "/login")
        self.end_headers()

    def do_GET(self):
        if self.path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_LOGIN.encode("utf-8"))
            return

        if not self._autenticado():
            self._redirecionar_login()
            return

        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGINA.encode("utf-8"))

        elif self.path == "/ranking":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_RANKING.encode("utf-8"))

        elif self.path == "/api/estado":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(estado).encode("utf-8"))

        elif self.path == "/api/ranking/estado":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(estado["ranking"]).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/login":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            if body.get("senha") == SENHA:
                token = secrets.token_hex(16)
                SESSIONS.add(token)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":false}')
            return

        if not self._autenticado():
            self._redirecionar_login()
            return

        if self.path == "/api/iniciar":
            if not estado["monitorando"]:
                estado["monitorando"] = True
                estado["status"] = "ativo"
                t = threading.Thread(target=loop_monitoramento, daemon=True)
                t.start()
            self._ok()

        elif self.path == "/api/parar":
            estado["monitorando"] = False
            estado["status"] = "parado"
            estado["hash_anterior"] = None
            self._ok()

        elif self.path == "/api/verificar":
            def verificar_agora():
                agora = datetime.now(TZ_BRASILIA).strftime("%d/%m/%Y %H:%M:%S")
                hash_atual, info = obter_hash_pagina(estado["url"])
                estado["ultima_verificacao"] = agora
                if hash_atual is None:
                    estado["historico"].insert(0, {"hora": agora, "tipo": "erro", "msg": str(info)})
                elif estado["hash_anterior"] is None:
                    estado["hash_anterior"] = hash_atual
                    estado["historico"].insert(0, {"hora": agora, "tipo": "inicio", "msg": "Hash inicial capturado"})
                elif hash_atual != estado["hash_anterior"]:
                    estado["hash_anterior"] = hash_atual
                    estado["ultima_mudanca"] = agora
                    estado["total_mudancas"] += 1
                    estado["historico"].insert(0, {"hora": agora, "tipo": "mudanca", "msg": "Página atualizada!"})
                else:
                    estado["historico"].insert(0, {"hora": agora, "tipo": "ok", "msg": "Sem mudanças"})
                estado["historico"] = estado["historico"][:50]
            threading.Thread(target=verificar_agora, daemon=True).start()
            self._ok()

        elif self.path == "/api/ranking/iniciar":
            est = estado["ranking"]
            if not est["monitorando"]:
                est["monitorando"] = True
                threading.Thread(target=loop_ranking, daemon=True).start()
            self._ok()

        elif self.path == "/api/ranking/parar":
            estado["ranking"]["monitorando"] = False
            self._ok()

        elif self.path == "/api/ranking/verificar":
            def _verificar():
                est = estado["ranking"]
                agora = datetime.now(TZ_BRASILIA).strftime("%d/%m/%Y %H:%M:%S")
                dados, erro = obter_dados_ranking()
                est["ultima_verificacao"] = agora
                est["dados"] = dados
                if dados is None:
                    est["historico"].insert(0, {"hora": agora, "tipo": "erro", "msg": erro})
                else:
                    est["historico"].insert(0, {"hora": agora, "tipo": "ok", "msg": "Verificação manual"})
                est["historico"] = est["historico"][:50]
            threading.Thread(target=_verificar, daemon=True).start()
            self._ok()

        elif self.path == "/api/iniciar-tudo":
            if not estado["monitorando"]:
                estado["monitorando"] = True
                estado["status"] = "ativo"
                threading.Thread(target=loop_monitoramento, daemon=True).start()
            if not estado["ranking"]["monitorando"]:
                estado["ranking"]["monitorando"] = True
                threading.Thread(target=loop_ranking, daemon=True).start()
            self._ok()

        elif self.path == "/api/ranking/limpar-historico":
            estado["ranking"]["historico"] = []
            self._ok()

        elif self.path == "/api/limpar-historico":
            estado["historico"] = []
            self._ok()

        elif self.path == "/api/testar-telegram":
            ok = enviar_telegram(f"✅ Teste do Monitor\nBot funcionando corretamente!\nURL: {estado['url']}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "erro": "" if ok else "Verifique o token e chat_id"}).encode())

        elif self.path == "/api/testar-email":
            ok = enviar_email(
                "📧 Teste do Monitor de Página",
                f"<h2>Teste de Email</h2><p>O monitor está funcionando corretamente.</p><p>URL monitorada: <a href='{estado['url']}'>{estado['url']}</a></p>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "erro": "" if ok else "Verifique as credenciais de email"}).encode())

        elif self.path == "/api/intervalo":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            estado["intervalo"] = max(10, int(body.get("intervalo", 60)))
            self._ok()
        else:
            self.send_response(404)
            self.end_headers()

    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


if __name__ == "__main__":
    print(f"🔍 Monitor de Página FCC Concursos")
    print(f"🌐 Acesse: http://localhost:{PORTA_SERVIDOR}")
    print(f"📧 Email: {EMAIL_DESTINO}")
    if not EMAIL_REMETENTE:
        print(f"⚠️  Email não configurado. Edite EMAIL_REMETENTE e EMAIL_SENHA_APP no arquivo.")
    print(f"Pressione Ctrl+C para parar\n")

    server = HTTPServer(("0.0.0.0", PORTA_SERVIDOR), Handler)
    threading.Thread(target=loop_autoping, daemon=True).start()
    server.serve_forever()
