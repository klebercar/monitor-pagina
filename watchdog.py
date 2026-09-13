"""
watchdog.py — Monitora se o monitor.py está vivo e notifica via Telegram se cair
Uso: python watchdog.py
"""

import urllib.request
import urllib.parse
import time
import json

TELEGRAM_TOKEN = "8826569151:AAHA0xAkp10HXKBApPc6Cdp7lU7qP4qaa24"
TELEGRAM_CHAT_ID = "917318112"
MONITOR_URL = "http://localhost:5000/api/estado"
HEARTBEAT_TIMEOUT = 120  # segundos sem heartbeat = monitor travado
CHECK_INTERVAL = 30      # checar a cada 30s


def enviar_telegram(mensagem):
    try:
        dados = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=dados)
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[WATCHDOG] Erro ao enviar Telegram: {e}")


def checar_monitor():
    try:
        with urllib.request.urlopen(MONITOR_URL, timeout=10) as r:
            estado = json.loads(r.read())

        if not estado.get("monitorando"):
            return "parado", 0

        heartbeat = estado.get("heartbeat")
        if heartbeat is None:
            return "sem_heartbeat", 0

        atraso = time.time() - heartbeat
        return "ok", atraso

    except Exception as e:
        return "offline", 0


def main():
    print("[WATCHDOG] Iniciado. Monitorando o monitor.py...")
    enviar_telegram("🐕 Watchdog iniciado! Vou te avisar se o monitor cair.")

    falhas_offline = 0
    alerta_travado_enviado = False

    while True:
        status, atraso = checar_monitor()

        if status == "offline":
            falhas_offline += 1
            print(f"[WATCHDOG] Monitor offline (tentativa {falhas_offline})")
            if falhas_offline >= 3:
                enviar_telegram("🚨 ALERTA: O monitor.py está OFFLINE!\nReinicie com: python monitor.py")
                falhas_offline = 0
        else:
            falhas_offline = 0

        if status == "ok" and atraso > HEARTBEAT_TIMEOUT:
            if not alerta_travado_enviado:
                enviar_telegram(f"⚠️ ALERTA: O monitor parece travado!\nÚltimo heartbeat há {int(atraso)}s.\nReinicie com: python monitor.py")
                alerta_travado_enviado = True
        else:
            alerta_travado_enviado = False

        if status == "ok":
            print(f"[WATCHDOG] Monitor OK. Heartbeat há {int(atraso)}s.")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
