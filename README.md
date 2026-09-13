# Monitor FCC — Auditor Fiscal TI

Monitora a página do concurso FCC e o ranking da planilha Google Sheets, notificando via Telegram quando houver mudanças.

## Como usar

1. Instale o Python 3.8+
2. Clone o repositório
3. Edite as configurações no topo do `monitor.py`:
   - `EMAIL_REMETENTE` / `EMAIL_SENHA_APP`
   - `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID`
4. Rode:

```
python monitor.py
```

5. Acesse `http://localhost:5000`

## Watchdog (opcional)

Em outro terminal, rode para monitorar se o monitor principal caiu:

```
python watchdog.py
```

## Funcionalidades

- Monitora página HTML por mudanças de conteúdo
- Monitora ranking na planilha Google Sheets
- Recalcula posição com base no total de pontos (incluindo títulos)
- Detecta candidatos sem título que podem te ultrapassar
- Notificações via Telegram e Email
- Painel web com histórico, heartbeat e contador regressivo
