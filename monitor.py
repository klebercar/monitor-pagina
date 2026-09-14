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
EMAIL_REMETENTE = "klebercar@gmail.com"
EMAIL_SENHA_APP = "vmkx hziv chuo ktjo"

TELEGRAM_TOKEN = "8826569151:AAHA0xAkp10HXKBApPc6Cdp7lU7qP4qaa24"
TELEGRAM_CHAT_ID = "917318112"

PORTA_SERVIDOR = int(os.environ.get("PORT", 5000))

SENHA = "kleberanny"
SESSIONS = set()

PLANILHA_ID = "1_I1TbFTI54YLq1sSpMWQcSGKupaFgbbPPVnh1UZ4Ig4"
PLANILHA_GID = "1992002210"
PLANILHA_NOME = "Kleber Ribeiro Carneiro"
PLANILHA_INSCRICAO = "0020697a"
# =======================================================

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
        "media_custom": None,
    },
    "pregressa": {},
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

        # Média de títulos de quem já preencheu
        titulos_vals = []
        for linha in reader[5:]:
            if len(linha) > 8 and linha[8]:
                tit = linha[8].strip()
                if tit and tit not in ("0,00", "0.00", "-", ""):
                    try:
                        titulos_vals.append(float(tit.replace(",", ".")))
                    except:
                        pass
        media_titulos_real = round(sum(titulos_vals) / len(titulos_vals), 2) if titulos_vals else 0.0
        media_titulos_usar = estado["ranking"].get("media_custom") if estado["ranking"].get("media_custom") is not None else media_titulos_real
        media_titulos = media_titulos_real

        # Ranking projetado: quem não tem título recebe a média
        todos_proj = []
        for linha in reader[5:]:
            if len(linha) > 9 and linha[0] and linha[9]:
                try:
                    obj_disc = float(linha[6].replace(",", ".")) + float(linha[7].replace(",", "."))
                    tit = linha[8].strip()
                    sem_titulo = not tit or tit in ("0,00", "0.00", "-", "")
                    tit_val = 0.0
                    try:
                        tit_val = float(tit.replace(",", ".")) if not sem_titulo else media_titulos_usar
                    except:
                        tit_val = media_titulos_usar if sem_titulo else 0.0
                    total_proj = obj_disc + tit_val
                    todos_proj.append((total_proj, linha[0]))
                except:
                    pass
        todos_proj.sort(key=lambda x: -x[0])
        pos_projetada = next((i + 1 for i, (_, insc) in enumerate(todos_proj) if PLANILHA_INSCRICAO in insc), "-")

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
            "media_titulos": str(media_titulos_real).replace(".", ","),
            "media_titulos_usar": str(media_titulos_usar).replace(".", ","),
            "pos_projetada": str(pos_projetada),
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
            if dados["pos_projetada"] != ant["pos_projetada"]:
                mudancas.append(f"Posição Projetada: {ant['pos_projetada']}º → {dados['pos_projetada']}º")
            if dados["media_titulos"] != ant["media_titulos"]:
                mudancas.append(f"Média Títulos: {ant['media_titulos']} → {dados['media_titulos']}")
            if dados["com_titulos"] != ant["com_titulos"]:
                mudancas.append(f"Títulos Preenchidos: {ant['com_titulos']} → {dados['com_titulos']}")
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
                texto = f"📊 Ranking Atualizado!\n{PLANILHA_NOME}\n" + "\n".join(mudancas) + f"\nDetectado em: {agora}"
                enviar_telegram(texto, com_botao=True)
                enviar_email("📊 Ranking FCC Atualizado!", f"<h2>Ranking Atualizado!</h2><pre>{texto}</pre>")
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


def enviar_telegram(mensagem, com_botao=False):
    try:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}
        if com_botao:
            payload["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": "⏳ No aguardo...", "callback_data": "no_aguardo"}]]})
        dados = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=dados)
        urllib.request.urlopen(req, timeout=10)
        print("[TELEGRAM] Mensagem enviada!")
        return True
    except Exception as e:
        print(f"[TELEGRAM] Erro: {e}")
        return False


def loop_telegram_polling():
    offset = None
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": "callback_query"}
            if offset:
                params["offset"] = offset
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=35) as resp:
                data = json.loads(resp.read())
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                cb = update.get("callback_query")
                if cb and cb.get("data") == "no_aguardo":
                    cb_id = cb["id"]
                    # Responde o callback (remove o "carregando" do botão)
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                            data=urllib.parse.urlencode({"callback_query_id": cb_id}).encode()
                        ), timeout=10
                    )
                    # Envia a mensagem de resposta
                    enviar_telegram("⏳ No aguardo...")
        except Exception as e:
            print(f"[POLLING] Erro: {e}")
            time.sleep(5)


def enviar_email(assunto, corpo):
    if not EMAIL_REMETENTE or not EMAIL_SENHA_APP:
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
        time.sleep(840)


def loop_monitoramento():
    print(f"[MONITOR] Iniciando monitoramento de: {estado['url']}")
    while estado["monitorando"]:
        estado["heartbeat"] = time.time()
        agora = datetime.now(TZ_BRASILIA).strftime("%d/%m/%Y %H:%M:%S")
        hash_atual, info = obter_hash_pagina(estado["url"])
        estado["ultima_verificacao"] = agora

        if hash_atual is None:
            estado["historico"].insert(0, {"hora": agora, "tipo": "erro", "msg": str(info)})
        elif estado["hash_anterior"] is None:
            estado["hash_anterior"] = hash_atual
            estado["historico"].insert(0, {"hora": agora, "tipo": "inicio", "msg": "Monitoramento iniciado"})
        elif hash_atual != estado["hash_anterior"]:
            estado["hash_anterior"] = hash_atual
            estado["ultima_mudanca"] = agora
            estado["total_mudancas"] += 1
            estado["historico"].insert(0, {"hora": agora, "tipo": "mudanca", "msg": "Página atualizada!"})
            texto = f"⚡ Página do Concurso FCC Atualizada!\nDetectado em: {agora}\nTotal de mudanças: {estado['total_mudancas']}\n{estado['url']}"
            enviar_telegram(texto, com_botao=True)
            enviar_email("⚡ Página do Concurso FCC Atualizada!", f"<h2>Página Atualizada!</h2><pre>{texto}</pre>")
        else:
            estado["historico"].insert(0, {"hora": agora, "tipo": "ok", "msg": "Sem mudanças"})

        estado["historico"] = estado["historico"][:50]

        for i in range(estado["intervalo"], 0, -1):
            if not estado["monitorando"]:
                return
            estado["proxima_em"] = i
            estado["heartbeat"] = time.time()
            time.sleep(1)
        estado["proxima_em"] = 0


def _nav(ativo):
    links = [("/", "🔍 Página FCC", "p"), ("/ranking", "📊 Meu Ranking", "r"), ("/cronograma", "📅 Cronograma", "c"), ("/titulos", "📎 Títulos", "t"), ("/pregressa", "📋 Vida Pregressa", "v")]
    itens = ""
    for href, label, key in links:
        cor = "#e2e8f0" if key == ativo else "#94a3b8"
        borda = "#3b82f6" if key == ativo else "transparent"
        itens += f'<a href="{href}" style="padding:12px 24px;font-size:13px;font-weight:600;color:{cor};text-decoration:none;border-bottom:2px solid {borda};">{label}</a>'
    return f'<nav style="background:#1e293b;border-bottom:1px solid #334155;display:flex;align-items:center;flex-wrap:wrap;">{itens}<button onclick="iniciarTudo()" style="margin-left:12px;padding:6px 16px;border-radius:8px;border:none;background:#10b981;color:white;font-size:13px;font-weight:600;cursor:pointer;">▶▶ Iniciar Tudo</button></nav>'


HTML_PREGRESSA = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vida Pregressa — FCC TI</title>
<style>
  * { margin:0;padding:0;box-sizing:border-box; }
  body { font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh; }
  .header { background:#1e293b;padding:20px 32px;border-bottom:1px solid #334155;display:flex;align-items:center;gap:12px; }
  .header h1 { font-size:20px;font-weight:700; }
  .container { max-width:900px;margin:32px auto;padding:0 20px; }
  .painel { background:#1e293b;border-radius:12px;border:1px solid #334155;padding:24px;margin-bottom:24px; }
  .painel h2 { font-size:14px;color:#94a3b8;margin-bottom:16px; }
  .doc-card { border:1px solid #334155;border-radius:10px;margin-bottom:12px;overflow:hidden; }
  .doc-top { display:flex;align-items:center;gap:12px;padding:14px 16px;background:#0f172a; }
  .doc-num { width:36px;height:28px;border-radius:6px;background:#1e293b;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#64748b;flex-shrink:0; }
  .doc-titulo { flex:1;font-size:13px;font-weight:600; }
  .status-btn { padding:5px 12px;border-radius:20px;border:none;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap; }
  .s-pendente { background:#334155;color:#94a3b8; }
  .s-pronto { background:#14532d;color:#4ade80; }
  .s-atencao { background:#422006;color:#fbbf24; }
  .doc-body { padding:14px 16px;border-top:1px solid #334155; }
  .campo-row { display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px; }
  .campo { flex:1;min-width:160px; }
  .campo label { display:block;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px; }
  .campo input { width:100%;padding:7px 10px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px; }
  .campo textarea { width:100%;padding:7px 10px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px;resize:vertical;min-height:52px; }
  .validade-alerta { font-size:11px;margin-top:6px;padding:5px 10px;border-radius:6px; }
  .val-ok { background:#14532d;color:#4ade80; }
  .val-warn { background:#422006;color:#fbbf24; }
  .val-venc { background:#7f1d1d;color:#fca5a5; }
  .resumo-bar { display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px; }
  .resumo-card { flex:1;min-width:100px;background:#1e293b;border-radius:10px;border:1px solid #334155;padding:16px;text-align:center; }
  .resumo-card .num { font-size:28px;font-weight:700; }
  .resumo-card .lab { font-size:11px;color:#64748b;margin-top:4px; }
  .elim { background:#1e293b;border-radius:12px;border:1px solid #7f1d1d;padding:20px;margin-bottom:24px; }
  .elim h2 { font-size:14px;color:#fca5a5;margin-bottom:14px; }
  .elim-item { display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #334155;font-size:13px;align-items:flex-start; }
  .elim-item:last-child { border-bottom:none; }
  .btn-salvar { padding:10px 24px;border-radius:8px;border:none;background:#3b82f6;color:white;font-size:13px;font-weight:600;cursor:pointer;margin-top:8px; }
  .aviso-90 { background:#1e293b;border:1px solid #f59e0b;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:12px;color:#fcd34d; }
</style>
</head>
<body>
<div class="header"><span>📋</span><h1>Sindicância da Vida Pregressa — FCC Auditor Fiscal TI</h1></div>
__NAV__
<div class="container">
  <div class="aviso-90">⚠️ Certidões com validade de <strong>90 dias</strong> devem ser expedidas no máximo 90 dias antes da data de entrega fixada no edital (prevista para 13–15/10/2026). Planeje a emissão com antecedência.</div>
  <div class="resumo-bar">
    <div class="resumo-card"><div class="num" id="cnt-pronto" style="color:#4ade80">0</div><div class="lab">✅ Prontos</div></div>
    <div class="resumo-card"><div class="num" id="cnt-pendente" style="color:#94a3b8">0</div><div class="lab">⏳ Pendentes</div></div>
    <div class="resumo-card"><div class="num" id="cnt-atencao" style="color:#fbbf24">0</div><div class="lab">⚠️ Atenção</div></div>
    <div class="resumo-card"><div class="num" id="cnt-total" style="color:#3b82f6">0</div><div class="lab">Total</div></div>
  </div>
  <div class="painel">
    <h2>📂 Documentos Exigidos — item 11.2 (clique no status para alterar)</h2>
    <div id="docs-container"></div>
    <button class="btn-salvar" onclick="salvar()">💾 Salvar progresso</button>
  </div>
  <div class="elim">
    <h2>🚨 Itens Eliminatórios — item 11.3 (verifique se nenhum se aplica a você)</h2>
    <div class="elim-item"><div>✅</div><div>Condenado em ação penal transitada em julgado ou excluído do serviço público por processo disciplinar</div></div>
    <div class="elim-item"><div>✅</div><div>Possui registros criminais</div></div>
    <div class="elim-item"><div>✅</div><div>Declaração falsa ou omissão de registro relevante sobre vida pregressa</div></div>
    <div class="elim-item"><div>✅</div><div>Prática de ato atentatório à moral e aos bons costumes</div></div>
    <div class="elim-item"><div>✅</div><div>Demissão por justa causa nos termos da legislação trabalhista</div></div>
    <div class="elim-item"><div>✅</div><div>Figurar como autor em inquérito policial, TCO ou procedimento disciplinar, ou como réu em ação penal</div></div>
    <div style="margin-top:12px;font-size:12px;color:#64748b">Se nenhum item acima se aplica, você está apto para a etapa de Sindicância.</div>
  </div>
</div>
<script>
async function iniciarTudo() { await fetch('/api/iniciar-tudo', { method: 'POST' }); }
const DOCS = [
  {id:'jf',  num:'I-a', titulo:'Certidão de antecedentes criminais — Justiça Federal', val90:true, obs:'Cidade onde reside/residiu nos últimos 5 anos'},
  {id:'je',  num:'I-b', titulo:'Certidão de antecedentes criminais — Justiça Estadual (CE)', val90:true, obs:'Cidade onde reside/residiu nos últimos 5 anos'},
  {id:'jmf', num:'I-c', titulo:'Certidão de antecedentes criminais — Justiça Militar Federal', val90:true, obs:'Inclusive para candidatos do sexo feminino'},
  {id:'jme', num:'I-d', titulo:'Certidão de antecedentes criminais — Justiça Militar Estadual', val90:true, obs:'Inclusive para candidatos do sexo feminino'},
  {id:'jel', num:'II',  titulo:'Certidão de antecedentes criminais — Justiça Eleitoral', val90:false, obs:''},
  {id:'prot',num:'III', titulo:'Certidões dos cartórios de protestos de títulos', val90:false, obs:'Cidade onde reside/residiu nos últimos 5 anos'},
  {id:'exec',num:'IV',  titulo:'Certidões dos cartórios de execução cível', val90:false, obs:'Cidade onde reside/residiu nos últimos 5 anos'},
  {id:'dec1',num:'V',   titulo:'Declaração do candidato — cidades onde residiu nos últimos 5 anos', val90:false, obs:'Própria declaração assinada'},
  {id:'dec2',num:'VI',  titulo:'Declaração do candidato — sem condenação definitiva nem punição disciplinar', val90:false, obs:'Declaração firmada pelo próprio candidato'},
  {id:'pol', num:'VII', titulo:'Folha de antecedentes / atestado expedido pela polícia estadual', val90:true, obs:'Expedida no máximo há 90 dias'},
  {id:'org', num:'VIII',titulo:'Declaração do órgão público — sem punição por improbidade administrativa', val90:true, obs:'Expedida no máximo há 90 dias. Aplica-se a servidores públicos.'},
];
let dados = {};
async function carregar() {
  const r = await fetch('/api/pregressa');
  dados = await r.json();
  renderDocs();
  atualizarResumo();
}
function renderDocs() {
  document.getElementById('docs-container').innerHTML = DOCS.map(doc => {
    const d = dados[doc.id] || {status:'pendente', data_emissao:'', obs:''};
    return `<div class="doc-card">
      <div class="doc-top">
        <div class="doc-num">${doc.num}</div>
        <div class="doc-titulo">${doc.titulo}${doc.obs?'<div style="font-size:11px;color:#64748b;font-weight:400;margin-top:2px">'+doc.obs+'</div>':''}</div>
        <button class="status-btn s-${d.status}" id="btn-${doc.id}" onclick="toggleStatus('${doc.id}')">${labelStatus(d.status)}</button>
      </div>
      <div class="doc-body">
        <div class="campo-row">
          ${doc.val90?`<div class="campo" style="max-width:200px"><label>Data de emissão</label><input type="date" id="data-${doc.id}" value="${d.data_emissao||''}" oninput="atualizarValidade('${doc.id}')"></div>`:''}
          <div class="campo"><label>Observações / protocolo</label><textarea id="obs-${doc.id}">${d.obs||''}</textarea></div>
        </div>
        ${doc.val90?`<div class="validade-alerta" id="val-${doc.id}"></div>`:''}
      </div>
    </div>`;
  }).join('');
  DOCS.forEach(doc => { if (doc.val90) atualizarValidade(doc.id); });
}
function labelStatus(s) { return s==='pronto'?'✅ Pronto':s==='atencao'?'⚠️ Atenção':'⏳ Pendente'; }
function toggleStatus(id) {
  const ciclo = ['pendente','pronto','atencao'];
  if (!dados[id]) dados[id] = {status:'pendente',data_emissao:'',obs:''};
  dados[id].status = ciclo[(ciclo.indexOf(dados[id].status)+1)%ciclo.length];
  const btn = document.getElementById('btn-'+id);
  btn.className = 'status-btn s-'+dados[id].status;
  btn.textContent = labelStatus(dados[id].status);
  atualizarResumo();
}
function atualizarValidade(id) {
  const input = document.getElementById('data-'+id);
  const el = document.getElementById('val-'+id);
  if (!input||!el) return;
  if (!input.value) { el.textContent=''; el.className='validade-alerta'; return; }
  const dias = Math.floor((new Date()-new Date(input.value))/86400000);
  if (dias>90) { el.textContent='❌ Certidão vencida! Emitida há '+dias+' dias (limite: 90)'; el.className='validade-alerta val-venc'; }
  else if (dias>60) { el.textContent='⚠️ Atenção: emitida há '+dias+' dias. Verifique a data de entrega.'; el.className='validade-alerta val-warn'; }
  else { el.textContent='✅ Válida: emitida há '+dias+' dias'; el.className='validade-alerta val-ok'; }
  if (dados[id]) dados[id].data_emissao = input.value;
}
function atualizarResumo() {
  let p=0,pe=0,a=0;
  DOCS.forEach(doc => { const s=(dados[doc.id]||{}).status||'pendente'; if(s==='pronto')p++; else if(s==='atencao')a++; else pe++; });
  document.getElementById('cnt-pronto').textContent=p;
  document.getElementById('cnt-pendente').textContent=pe;
  document.getElementById('cnt-atencao').textContent=a;
  document.getElementById('cnt-total').textContent=DOCS.length;
}
async function salvar() {
  DOCS.forEach(doc => {
    if (!dados[doc.id]) dados[doc.id]={status:'pendente',data_emissao:'',obs:''};
    const o=document.getElementById('obs-'+doc.id); if(o) dados[doc.id].obs=o.value;
    const d=document.getElementById('data-'+doc.id); if(d) dados[doc.id].data_emissao=d.value;
  });
  await fetch('/api/pregressa',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(dados)});
  const btn=document.querySelector('.btn-salvar'); btn.textContent='✅ Salvo!';
  setTimeout(()=>btn.textContent='💾 Salvar progresso',2000);
}
carregar();
</script>
</body>
</html>
""".replace("__NAV__", _nav("v"))

HTML_TITULOS = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Títulos — FCC TI</title>
<style>
  * { margin:0;padding:0;box-sizing:border-box; }
  body { font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh; }
  .header { background:#1e293b;padding:20px 32px;border-bottom:1px solid #334155;display:flex;align-items:center;gap:12px; }
  .header h1 { font-size:20px;font-weight:700; }
  .container { max-width:900px;margin:32px auto;padding:0 20px; }
  .painel { background:#1e293b;border-radius:12px;border:1px solid #334155;padding:24px;margin-bottom:24px; }
  .painel h2 { font-size:14px;color:#94a3b8;margin-bottom:16px;display:flex;align-items:center;gap:8px; }
  .alinea { border:1px solid #334155;border-radius:10px;margin-bottom:16px;overflow:hidden; }
  .alinea-header { display:flex;align-items:center;gap:12px;padding:14px 16px;background:#0f172a;cursor:pointer; }
  .alinea-letra { width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;flex-shrink:0; }
  .alinea-titulo { flex:1;font-size:13px;font-weight:600; }
  .alinea-pts { font-size:13px;font-weight:700;white-space:nowrap; }
  .alinea-body { padding:16px;border-top:1px solid #334155;display:none; }
  .alinea-body.aberto { display:block; }
  .campo { margin-bottom:12px; }
  .campo label { display:block;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px; }
  .campo input[type=number], .campo input[type=text] { width:100%;padding:8px 12px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px; }
  .campo-row { display:flex;gap:12px; }
  .campo-row .campo { flex:1; }
  .resultado { background:#0f172a;border-radius:12px;border:1px solid #334155;padding:20px;margin-bottom:24px; }
  .resultado h2 { font-size:14px;color:#94a3b8;margin-bottom:16px; }
  .res-linha { display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #1e293b;font-size:13px; }
  .res-linha:last-child { border-bottom:none; }
  .res-chave { color:#64748b; }
  .res-val { font-weight:700; }
  .total-box { background:#1e3a5f;border:1px solid #3b82f6;border-radius:10px;padding:16px 20px;display:flex;justify-content:space-between;align-items:center;margin-bottom:24px; }
  .total-box .label { font-size:13px;color:#93c5fd; }
  .total-box .valor { font-size:28px;font-weight:700;color:#3b82f6; }
  .docs { background:#1e293b;border-radius:12px;border:1px solid #334155;padding:24px;margin-bottom:24px; }
  .docs h2 { font-size:14px;color:#94a3b8;margin-bottom:16px; }
  .doc-item { display:flex;gap:10px;padding:10px 0;border-bottom:1px solid #334155;font-size:13px;align-items:flex-start; }
  .doc-item:last-child { border-bottom:none; }
  .doc-icon { font-size:16px;flex-shrink:0;margin-top:1px; }
  .doc-texto { flex:1; }
  .doc-obs { font-size:11px;color:#64748b;margin-top:3px; }
  .aviso-max { background:#422006;border:1px solid #f59e0b;border-radius:8px;padding:10px 14px;font-size:12px;color:#fcd34d;margin-top:8px;display:none; }
  .aviso-max.visivel { display:block; }
  .badge-max { background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:20px;margin-left:6px; }
</style>
</head>
<body>
<div class="header"><span>📎</span><h1>Calculadora de Títulos — FCC Auditor Fiscal TI</h1></div>
__NAV__
<div class="container">

  <div class="painel">
    <h2>📊 Pontuação por Alínea — clique para expandir e editar</h2>

    <!-- ALINEA A -->
    <div class="alinea">
      <div class="alinea-header" onclick="toggle('a')">
        <div class="alinea-letra" style="background:#1e3a5f;color:#3b82f6">A</div>
        <div class="alinea-titulo">Doutorado relacionado ao cargo (MEC)</div>
        <div class="alinea-pts" id="pts-a" style="color:#64748b">0,00 pts</div>
      </div>
      <div class="alinea-body" id="body-a">
        <div class="campo">
          <label>Possui diploma/certificado de Doutorado?</label>
          <select onchange="calcular()" id="sel-a" style="width:100%;padding:8px 12px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px">
            <option value="0">Não</option>
            <option value="4.8">Sim — +4,80 pts</option>
          </select>
        </div>
        <div class="doc-item" id="doc-a" style="display:none">
          <div class="doc-icon">📄</div>
          <div class="doc-texto">Diploma ou certificado/declaração de conclusão de Doutorado <strong>acompanhado de histórico escolar</strong>, expedido por instituição reconhecida pelo MEC.<div class="doc-obs">Formatos aceitos: JPEG, JPG, PNG ou PDF. Máx 5 MB por arquivo.</div></div>
        </div>
      </div>
    </div>

    <!-- ALINEA B -->
    <div class="alinea">
      <div class="alinea-header" onclick="toggle('b')">
        <div class="alinea-letra" style="background:#1e3a5f;color:#3b82f6">B</div>
        <div class="alinea-titulo">Mestrado relacionado ao cargo (MEC)</div>
        <div class="alinea-pts" id="pts-b" style="color:#64748b">0,00 pts</div>
      </div>
      <div class="alinea-body" id="body-b">
        <div class="campo">
          <label>Possui diploma/certificado de Mestrado?</label>
          <select onchange="calcular()" id="sel-b" style="width:100%;padding:8px 12px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px">
            <option value="0">Não</option>
            <option value="2.4">Sim — +2,40 pts</option>
          </select>
        </div>
        <div class="doc-item" id="doc-b" style="display:none">
          <div class="doc-icon">📄</div>
          <div class="doc-texto">Diploma ou certificado/declaração de conclusão de Mestrado <strong>acompanhado de histórico escolar</strong>, expedido por instituição reconhecida pelo MEC.<div class="doc-obs">Formatos aceitos: JPEG, JPG, PNG ou PDF. Máx 5 MB por arquivo.</div></div>
        </div>
      </div>
    </div>

    <!-- ALINEA C -->
    <div class="alinea">
      <div class="alinea-header" onclick="toggle('c')">
        <div class="alinea-letra" style="background:#1e3a5f;color:#3b82f6">C</div>
        <div class="alinea-titulo">Especialização ≥360h relacionada ao cargo</div>
        <div class="alinea-pts" id="pts-c" style="color:#64748b">0,00 pts</div>
      </div>
      <div class="alinea-body" id="body-c">
        <div class="campo">
          <label>Quantidade de certificados de Especialização (máx 2, vale 1,20 cada)</label>
          <input type="number" id="qtd-c" min="0" max="2" value="0" oninput="calcular()">
        </div>
        <div class="doc-item" id="doc-c" style="display:none">
          <div class="doc-icon">📄</div>
          <div class="doc-texto">Certificado de pós-graduação em especialização com carga horária mínima de 360h, <strong>acompanhado de histórico escolar</strong>.<div class="doc-obs">Formatos aceitos: JPEG, JPG, PNG ou PDF. Máx 5 MB por arquivo.</div></div>
        </div>
      </div>
    </div>

    <!-- ALINEA D -->
    <div class="alinea">
      <div class="alinea-header" onclick="toggle('d')">
        <div class="alinea-letra" style="background:#14532d;color:#10b981">D</div>
        <div class="alinea-titulo">Aprovação em concurso público / processo seletivo</div>
        <div class="alinea-pts" id="pts-d" style="color:#10b981">0,80 pts ✓</div>
      </div>
      <div class="alinea-body aberto" id="body-d">
        <div class="campo">
          <label>Quantidade de aprovações (máx 2, vale 0,40 cada)</label>
          <input type="number" id="qtd-d" min="0" max="2" value="2" oninput="calcular()">
        </div>
        <div class="aviso-max visivel" id="max-d">⚠️ Limite máximo atingido: 0,80 pts</div>
        <div style="margin-top:12px">
          <div class="doc-item">
            <div class="doc-icon">📄</div>
            <div class="doc-texto"><strong>Para cada aprovação:</strong> Certidão expedida pelo setor de pessoal do órgão ou certificado do órgão executor, contendo: cargo concorrido, requisito/escolaridade, aprovação e/ou classificação.<div class="doc-obs">Alternativa: Diário Oficial com resultado final contendo cargo, escolaridade exigida, atribuições e classificação com identificação clara do candidato.</div></div>
          </div>
        </div>
      </div>
    </div>

    <!-- ALINEA E -->
    <div class="alinea">
      <div class="alinea-header" onclick="toggle('e')">
        <div class="alinea-letra" style="background:#14532d;color:#10b981">E</div>
        <div class="alinea-titulo">Exercício de atividade profissional de nível superior</div>
        <div class="alinea-pts" id="pts-e" style="color:#10b981">9,60 pts ✓</div>
      </div>
      <div class="alinea-body aberto" id="body-e">
        <p style="font-size:12px;color:#64748b;margin-bottom:12px">1,20 pts por ano completo, sem sobreposição de tempo. Máximo: 9,60 pts (8 anos).</p>
        <div id="periodos-e"></div>
        <button onclick="adicionarPeriodo()" style="margin-top:8px;padding:7px 16px;border-radius:6px;border:none;background:#334155;color:#e2e8f0;font-size:12px;cursor:pointer">➕ Adicionar período</button>
        <div class="aviso-max" id="max-e"></div>
        <div style="margin-top:16px">
          <div class="doc-item">
            <div class="doc-icon">📄</div>
            <div class="doc-texto"><strong>Instituição pública — enviar cumulativamente:</strong>
              <div class="doc-obs">a) Diploma do curso de nível superior (para verificar data de conclusão)</div>
              <div class="doc-obs">b) Declaração/certidão de tempo de serviço emitida pelo RH, informando: período (início e fim), escolaridade do cargo, espécie do serviço de nível superior e descrição das atividades.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="total-box">
    <div>
      <div class="label">Pontuação Total de Títulos</div>
      <div style="font-size:11px;color:#64748b;margin-top:4px">Limite máximo: 20,00 pts</div>
    </div>
    <div class="valor" id="total-geral">10,40</div>
  </div>

  <div class="resultado" id="resumo-box">
    <h2>📋 Resumo por Alínea</h2>
    <div class="res-linha"><span class="res-chave">A — Doutorado</span><span class="res-val" id="res-a">0,00</span></div>
    <div class="res-linha"><span class="res-chave">B — Mestrado</span><span class="res-val" id="res-b">0,00</span></div>
    <div class="res-linha"><span class="res-chave">C — Especialização</span><span class="res-val" id="res-c">0,00</span></div>
    <div class="res-linha"><span class="res-chave">D — Aprovações em concurso</span><span class="res-val" id="res-d">0,80</span></div>
    <div class="res-linha"><span class="res-chave">E — Experiência profissional</span><span class="res-val" id="res-e">9,60</span></div>
  </div>

  <div class="docs" id="docs-box">
    <h2>📂 Documentos que você precisa enviar</h2>
    <div id="docs-lista"></div>
    <div style="margin-top:12px;font-size:11px;color:#64748b">⚠️ Envio exclusivamente pelo site www.concursosfcc.com.br. Formatos: JPEG, JPG, PNG ou PDF. Máx 5 MB por arquivo. Não serão aceitos protocolos.</div>
  </div>

</div>
<script>
async function iniciarTudo() { await fetch('/api/iniciar-tudo', { method: 'POST' }); }

function toggle(id) {
  const b = document.getElementById('body-' + id);
  b.classList.toggle('aberto');
}

let periodos = [{inicio:'2010-01-01', fim:''}];

function adicionarPeriodo() {
  periodos.push({inicio:'', fim:''});
  renderPeriodos();
  calcular();
}

function removerPeriodo(i) {
  periodos.splice(i, 1);
  renderPeriodos();
  calcular();
}

function renderPeriodos() {
  const cont = document.getElementById('periodos-e');
  cont.innerHTML = periodos.map((p, i) => `
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
      <div style="flex:1">
        <label style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em">Início</label>
        <input type="date" value="${p.inicio}" onchange="periodos[${i}].inicio=this.value;calcular()" style="width:100%;padding:7px 10px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px">
      </div>
      <div style="flex:1">
        <label style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em">Fim (ou em branco se atual)</label>
        <input type="date" value="${p.fim}" onchange="periodos[${i}].fim=this.value;calcular()" style="width:100%;padding:7px 10px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px">
      </div>
      ${periodos.length > 1 ? `<button onclick="removerPeriodo(${i})" style="margin-top:16px;padding:7px 10px;border-radius:6px;border:none;background:#7f1d1d;color:#fca5a5;font-size:12px;cursor:pointer">✕</button>` : ''}
    </div>
  `).join('');
}

function calcularAnosE() {
  // Soma dias sem sobreposicao
  const hoje = new Date();
  const intervalos = periodos
    .filter(p => p.inicio)
    .map(p => ({
      ini: new Date(p.inicio),
      fim: p.fim ? new Date(p.fim) : hoje
    }))
    .filter(p => p.fim > p.ini)
    .sort((a, b) => a.ini - b.ini);

  // merge sobreposicoes
  const merged = [];
  for (const iv of intervalos) {
    if (merged.length && iv.ini <= merged[merged.length-1].fim) {
      merged[merged.length-1].fim = new Date(Math.max(merged[merged.length-1].fim, iv.fim));
    } else {
      merged.push({...iv});
    }
  }
  let dias = 0;
  for (const iv of merged) dias += (iv.fim - iv.ini) / 86400000;
  return Math.floor(dias / 365);
}

function fmt(v) { return v.toFixed(2).replace('.', ','); }

function calcular() {
  const a = parseFloat(document.getElementById('sel-a').value) || 0;
  const b = parseFloat(document.getElementById('sel-b').value) || 0;
  const c = Math.min(2, parseInt(document.getElementById('qtd-c').value) || 0) * 1.2;
  const d = Math.min(2, parseInt(document.getElementById('qtd-d').value) || 0) * 0.4;
  const anosE = calcularAnosE();
  const e = Math.min(9.6, anosE * 1.2);

  document.getElementById('pts-a').textContent = fmt(a) + ' pts' + (a > 0 ? ' ✓' : '');
  document.getElementById('pts-a').style.color = a > 0 ? '#10b981' : '#64748b';
  document.getElementById('pts-b').textContent = fmt(b) + ' pts' + (b > 0 ? ' ✓' : '');
  document.getElementById('pts-b').style.color = b > 0 ? '#10b981' : '#64748b';
  document.getElementById('pts-c').textContent = fmt(c) + ' pts' + (c > 0 ? ' ✓' : '');
  document.getElementById('pts-c').style.color = c > 0 ? '#10b981' : '#64748b';
  document.getElementById('pts-d').textContent = fmt(d) + ' pts' + (d > 0 ? ' ✓' : '');
  document.getElementById('pts-e').textContent = fmt(e) + ' pts (' + anosE + ' anos)' + (e > 0 ? ' ✓' : '');

  document.getElementById('doc-a').style.display = a > 0 ? 'flex' : 'none';
  document.getElementById('doc-b').style.display = b > 0 ? 'flex' : 'none';
  document.getElementById('doc-c').style.display = c > 0 ? 'flex' : 'none';
  document.getElementById('max-d').classList.toggle('visivel', d >= 0.8);
  const maxE = document.getElementById('max-e');
  if (e >= 9.6) { maxE.textContent = '⚠️ Limite máximo atingido: 9,60 pts (' + anosE + ' anos computados, mas só 8 são aproveitados)'; maxE.classList.add('visivel'); }
  else { maxE.classList.remove('visivel'); }

  const total = Math.min(20, a + b + c + d + e);
  document.getElementById('total-geral').textContent = fmt(total) + ' pts';
  document.getElementById('total-geral').style.color = total >= 10 ? '#10b981' : '#3b82f6';

  document.getElementById('res-a').textContent = fmt(a);
  document.getElementById('res-b').textContent = fmt(b);
  document.getElementById('res-c').textContent = fmt(c);
  document.getElementById('res-d').textContent = fmt(d);
  document.getElementById('res-e').textContent = fmt(e) + ' (' + anosE + ' anos)';

  // Documentos
  const docs = [];
  if (a > 0) docs.push({icon:'🎓', texto:'Diploma ou certificado de <strong>Doutorado</strong> + histórico escolar', obs:'Alínea A'});
  if (b > 0) docs.push({icon:'🎓', texto:'Diploma ou certificado de <strong>Mestrado</strong> + histórico escolar', obs:'Alínea B'});
  if (c > 0) docs.push({icon:'🎓', texto:`${Math.min(2,parseInt(document.getElementById('qtd-c').value)||0)} certificado(s) de <strong>Especialização</strong> (≥360h) + histórico escolar`, obs:'Alínea C'});
  if (d > 0) docs.push({icon:'📄', texto:`${Math.min(2,parseInt(document.getElementById('qtd-d').value)||0)} certidão(s) de <strong>aprovação em concurso</strong> (setor de pessoal ou Diário Oficial)`, obs:'Alínea D — deve conter: cargo, escolaridade exigida, aprovação/classificação'});
  if (e > 0) docs.push(
    {icon:'🎓', texto:'Diploma de curso de <strong>nível superior</strong> (para verificar data de conclusão)', obs:'Alínea E'},
    {icon:'📄', texto:'Declaração/certidão de <strong>tempo de serviço</strong> emitida pelo RH da instituição', obs:'Alínea E — deve conter: período (início e fim), escolaridade do cargo, espécie do serviço e descrição das atividades'}
  );
  document.getElementById('docs-lista').innerHTML = docs.length
    ? docs.map(d => `<div class="doc-item"><div class="doc-icon">${d.icon}</div><div class="doc-texto">${d.texto}<div class="doc-obs">${d.obs}</div></div></div>`).join('')
    : '<div style="color:#64748b;font-size:13px">Nenhum título selecionado.</div>';
}

renderPeriodos();
calcular();
</script>
</body>
</html>
""".replace("__NAV__", _nav("t"))

HTML_CRONOGRAMA = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cronograma — FCC TI</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .header { background: #1e293b; padding: 20px 32px; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 20px; font-weight: 700; }
  .container { max-width: 900px; margin: 32px auto; padding: 0 20px; }
  table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; border: 1px solid #334155; }
  th { background: #0f172a; padding: 12px 16px; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #64748b; text-align: left; }
  td { padding: 12px 16px; font-size: 13px; border-top: 1px solid #334155; vertical-align: top; }
  tr.hoje td { background: #1e3a5f; }
  tr.passado td { color: #475569; }
  tr.proximo td { background: #1a2e1a; }
  .num { color: #64748b; font-size: 12px; width: 36px; }
  .data { white-space: nowrap; font-weight: 600; min-width: 140px; }
  .data.passada { color: #475569; }
  .data.hoje-c { color: #f59e0b; }
  .data.proxima { color: #10b981; }
  .data.futura { color: #3b82f6; }
  .badge-prox { display:inline-block;background:#10b981;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;margin-left:8px;vertical-align:middle; }
  .badge-hoje { display:inline-block;background:#f59e0b;color:#000;font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;margin-left:8px;vertical-align:middle; }
  .aviso { background:#1e293b;border:1px solid #334155;border-radius:12px;padding:12px 16px;margin-bottom:20px;font-size:12px;color:#64748b; }
  .destaque { font-weight: 700; color: #e2e8f0; }
</style>
</head>
<body>
<div class="header"><span>📅</span><h1>Cronograma — FCC Auditor Fiscal TI</h1></div>
__NAV__
<div class="container">
  <div class="aviso">* Cronograma sujeito a alterações. Fonte: Comunicado FCC (retificado).</div>
  <table>
    <thead><tr><th>#</th><th>Atividade</th><th>Data Prevista</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
<script>
async function iniciarTudo() { await fetch('/api/iniciar-tudo', { method: 'POST' }); }
const EVENTOS = [
  [17, "Publicação do Resultado Preliminar das Provas Objetivas e Discursivas", "2026-09-09", "2026-09-09"],
  [18, "Vista das Folhas de Respostas das Provas Objetiva e Discursiva", "2026-09-10", "2026-09-11"],
  [19, "Prazo para interposição de recursos quanto aos Resultados Preliminares das Provas Objetivas e Discursivas", "2026-09-10", "2026-09-11"],
  [20, "Publicação do Edital de Resultado Definitivo das Provas Objetiva e Discursiva e Convocação para Heteroidentificação, Avaliação Biopsicossocial, Sindicância da Vida Pregressa e Envio dos Títulos", "2026-10-07", "2026-10-07"],
  [21, "Prazo para envio dos Títulos para Avaliação", "2026-10-13", "2026-10-15"],
  [22, "Prazo para envio das documentações comprobatórias para Análise da Sindicância da Vida Pregressa", "2026-10-13", "2026-10-15"],
  [23, "Realização da Avaliação Biopsicossocial aos candidatos com deficiência", "2026-10-16", "2026-10-16"],
  [24, "Realização da Comissão de Heteroidentificação dos candidatos autodeclarados negros (pretos e pardos)", "2026-10-17", "2026-10-18"],
  [25, "Publicação do Edital de Resultado Preliminar da Comissão de Heteroidentificação e da Avaliação Biopsicossocial", "2026-10-28", "2026-10-28"],
  [26, "Prazo para interposição de recursos quanto Resultado Preliminar da Heteroidentificação e Avaliação Biopsicossocial", "2026-10-29", "2026-10-30"],
  [27, "Publicação do Edital de Resultado Definitivo da Heteroidentificação, Avaliação Biopsicossocial, Resultado Preliminar das Análises dos Títulos e da Sindicância da Vida Pregressa", "2026-11-25", "2026-11-25"],
  [28, "Prazo para interposição de recursos quanto Resultado Preliminar das Análises dos Títulos e da Sindicância da Vida Pregressa", "2026-11-26", "2026-11-27"],
  [29, "Publicação do Edital de Resultado Definitivo das Análises dos Títulos e da Sindicância da Vida Pregressa e RESULTADO FINAL do Concurso", "2026-12-18", "2026-12-18"],
];
function fmt(d) { return d.split('-').reverse().join('/'); }
const hoje = new Date().toISOString().slice(0, 10);
const tbody = document.getElementById('tbody');
let proximoMarcado = false;
EVENTOS.forEach(([num, desc, ini, fim]) => {
  const passado = fim < hoje;
  const ativo = ini <= hoje && hoje <= fim;
  const proximo = !passado && !ativo && !proximoMarcado;
  if (proximo) proximoMarcado = true;
  const trClass = ativo ? 'hoje' : passado ? 'passado' : proximo ? 'proximo' : '';
  const dataClass = ativo ? 'hoje-c' : passado ? 'passada' : proximo ? 'proxima' : 'futura';
  const dataStr = ini === fim ? fmt(ini) : fmt(ini) + ' a ' + fmt(fim);
  const badge = ativo ? '<span class="badge-hoje">HOJE</span>' : proximo ? '<span class="badge-prox">PRÓXIMO</span>' : '';
  tbody.innerHTML += `<tr class="${trClass}"><td class="num">${num}</td><td class="${passado?'':'destaque'}">${desc}</td><td class="data ${dataClass}">${dataStr}${badge}</td></tr>`;
});
</script>
</body>
</html>
"""


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
__NAV__
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
  <div class="painel" id="painel-proj" style="display:none">
    <h2>🔮 Projeção — Se todos receberem a média de títulos</h2>
    <div class="linha"><span class="chave">Média real (quem já preencheu)</span><span class="valor" id="d-media-tit" style="color:#a855f7">—</span></div>
    <div class="linha" style="align-items:center">
      <span class="chave">Média usada no cálculo</span>
      <span style="display:flex;gap:8px;align-items:center">
        <input type="number" id="input-media" step="0.01" min="0" max="20" style="width:80px;padding:6px 8px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:13px;text-align:center">
        <button onclick="salvarMedia()" style="padding:6px 14px;border-radius:6px;border:none;background:#a855f7;color:white;font-size:12px;font-weight:600;cursor:pointer">Recalcular</button>
        <button onclick="resetarMedia()" style="padding:6px 10px;border-radius:6px;border:none;background:#475569;color:white;font-size:12px;cursor:pointer" title="Usar média real">↺</button>
      </span>
    </div>
    <div class="linha"><span class="chave">Sua posição projetada</span><span class="valor" id="d-pos-proj" style="color:#10b981">—</span></div>
    <div class="linha"><span class="chave">Sua posição atual (c/ títulos reais)</span><span class="valor" id="d-pos-recalc2">—</span></div>
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
let ultimaMudanca = null, proximaVerificacao = null;
async function iniciarTudo() { await fetch('/api/iniciar-tudo', { method: 'POST' }); atualizar(); }
async function salvarMedia() {
  const val = document.getElementById('input-media').value;
  await fetch('/api/ranking/media', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ media: val }) });
  setTimeout(atualizar, 2000);
}
async function resetarMedia() {
  await fetch('/api/ranking/media', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ media: null }) });
  setTimeout(atualizar, 2000);
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
    // projeção
    document.getElementById('painel-proj').style.display = 'block';
    document.getElementById('d-media-tit').textContent = d.dados.media_titulos + ' pts (real)';
    const inputMedia = document.getElementById('input-media');
    if (inputMedia && inputMedia !== document.activeElement) {
      inputMedia.value = d.dados.media_titulos_usar.replace(',', '.');
    }
    document.getElementById('d-pos-proj').textContent = d.dados.pos_projetada + 'º de ' + d.dados.total_candidatos;
    document.getElementById('d-pos-recalc2').textContent = d.dados.pos_recalculada + 'º de ' + d.dados.total_candidatos;
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
""".replace("__NOME__", PLANILHA_NOME).replace("__NAV__", _nav("r"))


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
  .btn-verde { background: #10b981; color: white; } .btn-verde:hover { background: #059669; }
  .btn-vermelho { background: #ef4444; color: white; } .btn-vermelho:hover { background: #dc2626; }
  .btn-azul { background: #3b82f6; color: white; } .btn-azul:hover { background: #2563eb; }
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
<div class="header"><span>🔍</span><h1>Monitor de Página — FCC Concursos</h1></div>
__NAV__
<div class="container">
  <div class="alerta" id="alerta">⚡ <strong>Mudança detectada!</strong> A página foi atualizada.</div>
  <div class="url-box">🔗 <a href="__URL__" target="_blank">__URL__</a></div>
  <div class="cards">
    <div class="card" id="card-status"><div class="label">Status</div><div class="value" id="status-val">—</div></div>
    <div class="card amarelo"><div class="label">Total de Mudanças</div><div class="value" id="mudancas-val">0</div></div>
    <div class="card azul"><div class="label">Última Verificação</div><div class="value" style="font-size:13px;padding-top:4px;" id="verificacao-val">—</div></div>
    <div class="card verde"><div class="label">Última Mudança</div><div class="value" style="font-size:13px;padding-top:4px;" id="mudanca-val">—</div></div>
    <div class="card" id="card-http"><div class="label">Status HTTP</div><div class="value" id="http-val">—</div></div>
    <div class="card" id="card-heartbeat"><div class="label">Heartbeat</div><div class="value" style="font-size:13px;padding-top:4px;" id="heartbeat-val">—</div></div>
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
  <div style="background:#1e293b;border-radius:12px;padding:16px 20px;margin-bottom:24px;border:1px solid #334155;font-size:13px;color:#94a3b8;" id="countdown-box">
    ⏱ Próxima verificação em: <strong id="countdown-val">—</strong>
  </div>
  <div class="historico">
    <h2>📋 Histórico</h2>
    <div id="historico-lista"></div>
  </div>
</div>
<script>
let ultimaMudanca = null, proximaVerificacao = null;
async function iniciarTudo() { await fetch('/api/iniciar-tudo', { method: 'POST' }); atualizar(); }
async function iniciar() { await fetch('/api/iniciar', { method: 'POST' }); atualizar(); }
async function parar() { await fetch('/api/parar', { method: 'POST' }); atualizar(); }
async function verificarAgora() { await fetch('/api/verificar', { method: 'POST' }); setTimeout(atualizar, 2000); }
async function limparHistorico() { await fetch('/api/limpar-historico', { method: 'POST' }); atualizar(); }
async function salvarIntervalo() {
  const v = document.getElementById('intervalo-input').value;
  await fetch('/api/intervalo', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ intervalo: parseInt(v) }) });
}
async function testarEmail() {
  const r = await fetch('/api/testar-email', { method: 'POST' });
  const d = await r.json();
  alert(d.ok ? '✅ Email de teste enviado!' : '❌ Erro: ' + d.erro);
}
async function testarTelegram() {
  const r = await fetch('/api/testar-telegram', { method: 'POST' });
  const d = await r.json();
  alert(d.ok ? '✅ Mensagem enviada no Telegram!' : '❌ Erro: ' + d.erro);
}
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
  document.getElementById('http-val').textContent = http || '—';
  cardHttp.className = 'card ' + (http === 200 ? 'verde' : http ? 'vermelho' : '');
  const hb = d.heartbeat;
  const cardHb = document.getElementById('card-heartbeat');
  if (hb) {
    const seg = Math.round(Date.now() / 1000 - hb);
    document.getElementById('heartbeat-val').textContent = seg + 's atrás';
    cardHb.className = 'card ' + (seg < 10 ? 'verde' : seg < 30 ? 'amarelo' : 'vermelho');
  } else {
    document.getElementById('heartbeat-val').textContent = '—';
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
  document.getElementById('historico-lista').innerHTML = d.historico.map(h => `
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
atualizar();
setInterval(atualizar, 5000);
</script>
</body>
</html>
""".replace("__URL__", URL_MONITORADA).replace("__NAV__", _nav("p"))

HTML_CRONOGRAMA = HTML_CRONOGRAMA.replace("__NAV__", _nav("c"))



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

        elif self.path == "/pregressa":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PREGRESSA.encode("utf-8"))

        elif self.path == "/titulos":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TITULOS.encode("utf-8"))

        elif self.path == "/cronograma":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_CRONOGRAMA.encode("utf-8"))

        elif self.path == "/api/pregressa":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(estado["pregressa"]).encode("utf-8"))

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

        if self.path == "/api/pregressa":
            length = int(self.headers.get("Content-Length", 0))
            estado["pregressa"] = json.loads(self.rfile.read(length))
            self._ok()

        elif self.path == "/api/iniciar":
            if not estado["monitorando"]:
                estado["monitorando"] = True
                estado["status"] = "ativo"
                threading.Thread(target=loop_monitoramento, daemon=True).start()
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

        elif self.path == "/api/ranking/media":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            val = body.get("media")
            estado["ranking"]["media_custom"] = float(str(val).replace(",", ".")) if val not in (None, "") else None
            def _recalc():
                est = estado["ranking"]
                agora = datetime.now(TZ_BRASILIA).strftime("%d/%m/%Y %H:%M:%S")
                dados, erro = obter_dados_ranking()
                est["ultima_verificacao"] = agora
                est["dados"] = dados
                if dados:
                    est["historico"].insert(0, {"hora": agora, "tipo": "ok", "msg": f"Recalculado com média {estado['ranking']['media_custom']}"})
                    est["historico"] = est["historico"][:50]
            threading.Thread(target=_recalc, daemon=True).start()
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
                f"<h2>Teste de Email</h2><p>O monitor está funcionando.</p><p>URL: <a href='{estado['url']}'>{estado['url']}</a></p>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "erro": "" if ok else "Verifique as credenciais"}).encode())

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
    print(f"Pressione Ctrl+C para parar\n")
    server = HTTPServer(("0.0.0.0", PORTA_SERVIDOR), Handler)
    threading.Thread(target=loop_autoping, daemon=True).start()
    threading.Thread(target=loop_telegram_polling, daemon=True).start()
    server.serve_forever()
