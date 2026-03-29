"""
run_bot.py — Universal entrypoint for any provisioned Frank Bot.

On the client droplet this is what systemd runs:
    /opt/frankbot/venv/bin/python3 /opt/frankbot/app/run_bot.py

Config is loaded from /opt/frankbot/config.json + environment variables.
"""
import os, sys
from pathlib import Path

# Add app root to path
sys.path.insert(0, str(Path(__file__).parent))

# Load .env if present (dev/local use)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from shared.bot_config import BotConfig
from shared.frank_bot import FrankBot

config = BotConfig.from_env()

print(f"[Frank Bot] Starting — {config.company_name} ({config.bot_id})", flush=True)
print(f"[Frank Bot] Vertical: {config.vertical} | Tier: {config.tier} | Port: {config.port}", flush=True)

bot = FrankBot(config)
app = bot.create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=config.port,
        debug=False,
        threaded=True,
    )
