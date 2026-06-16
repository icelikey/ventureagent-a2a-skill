param(
    [string]$ApiUrl = $env:VENTUREAGENT_API_URL
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ApiUrl)) {
    $ApiUrl = "https://indices-britannica-competing-peer.trycloudflare.com"
}

Write-Host "VentureAgent API: $ApiUrl"

python scripts\ventureagent_a2a_client.py --api-url $ApiUrl health
python scripts\ventureagent_a2a_client.py --api-url $ApiUrl agent-card
python scripts\ventureagent_a2a_client.py --api-url $ApiUrl capabilities
python scripts\openclaw_a2a_validation.py --api-url $ApiUrl --retention-policy safe_summary_only
