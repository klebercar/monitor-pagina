# monitor.ps1 — Monitora uma página e notifica quando mudar

$URL = "https://www.concursosfcc.com.br/concursos/sface125/index.html"
$INTERVALO_SEGUNDOS = 60  # Verifica a cada 60 segundos

$conteudoAnterior = $null

function Notificar($titulo, $mensagem) {
    Add-Type -AssemblyName System.Windows.Forms
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.Visible = $true
    $notify.ShowBalloonTip(5000, $titulo, $mensagem, [System.Windows.Forms.ToolTipIcon]::Info)
    Start-Sleep -Seconds 6
    $notify.Dispose()
}

function ObterConteudo($url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 30 -UserAgent "Mozilla/5.0"
        return $response.Content
    } catch {
        Write-Host "[ERRO] Falha ao acessar a página: $_"
        return $null
    }
}

Write-Host "🔍 Monitorando: $URL"
Write-Host "⏱️  Intervalo: $INTERVALO_SEGUNDOS segundos"
Write-Host "Pressione Ctrl+C para parar`n"

while ($true) {
    $conteudoAtual = ObterConteudo $URL

    if ($conteudoAtual -ne $null) {
        if ($conteudoAnterior -eq $null) {
            $conteudoAnterior = $conteudoAtual
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Conteúdo inicial capturado."
        } elseif ($conteudoAtual -ne $conteudoAnterior) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ⚡ MUDANÇA DETECTADA!"
            Notificar "Página Atualizada!" "A página do Gurujá foi atualizada."
            $conteudoAnterior = $conteudoAtual
        } else {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Sem mudanças."
        }
    }

    Start-Sleep -Seconds $INTERVALO_SEGUNDOS
}
