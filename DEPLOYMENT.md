# Deployment & Security Guide

This bot ships with two ways to run it in production, both with
auto-restart baked in, plus a `/health` endpoint for uptime monitoring.

## Option A — Docker (recommended)

```bash
cp .env.example .env        # add your real DISCORD_BOT_TOKEN
docker compose up -d --build
```

- `restart: unless-stopped` — the container relaunches automatically if
  the bot crashes or the host reboots.
- The container runs as a non-root user, with a read-only filesystem and
  all Linux capabilities dropped (`docker-compose.yml`), so even if the
  bot process were somehow compromised, it can't modify itself, write
  arbitrary files, or escalate privileges.
- `HEALTHCHECK` in the `Dockerfile` polls `/health` every 30s. Check status
  with `docker ps` (look for "healthy"/"unhealthy") or `docker inspect`.

Update and redeploy:
```bash
git pull
docker compose up -d --build
```

## Option B — systemd (bare VPS, no Docker)

```bash
sudo useradd -r -m -s /usr/sbin/nologin botuser
sudo mkdir -p /opt/digimon-bot
sudo cp bot.py requirements.txt /opt/digimon-bot/
sudo cp .env /opt/digimon-bot/.env        # your real token
cd /opt/digimon-bot
sudo python3 -m venv venv
sudo ./venv/bin/pip install -r requirements.txt --break-system-packages
sudo chown -R botuser:botuser /opt/digimon-bot
sudo chmod 600 /opt/digimon-bot/.env

sudo cp digimon-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now digimon-bot
```

- `Restart=always` — systemd relaunches the bot on crash, with a 5s
  backoff and a burst limit so a persistent crash-loop doesn't hammer
  the box.
- The unit file runs the bot as an unprivileged user with `ProtectSystem`,
  `ProtectHome`, `NoNewPrivileges`, and several other sandboxing
  directives that restrict what the process can touch on disk and in
  the kernel — standard systemd hardening.
- Logs: `journalctl -u digimon-bot -f`

## Getting close to 100% uptime

No host can promise literal 100% (Discord itself has occasional outages
you can't engineer around), but this gets you very close:

1. **Auto-restart** — covered above by Docker/systemd. If the process
   dies for any reason, it's back within seconds.
2. **Auto-reconnect** — discord.py already handles gateway disconnects
   and network blips internally with exponential backoff; you don't need
   to add anything for this.
3. **External monitoring** — point a free uptime checker (UptimeRobot,
   BetterStack, healthchecks.io) at `http://your-server-ip:8080/health`
   on an interval (e.g. every 5 min) and set up an alert (email/SMS/Slack)
   if it goes down. This is what catches "the whole VPS fell over," which
   auto-restart alone can't fix.
4. **Pick a host with a real SLA** — Hetzner, DigitalOcean, and Linode all
   advertise 99.9%+ uptime SLAs on their VPS products. Avoid free-tier
   platforms that sleep on inactivity.
5. **Watch your dependency**: this bot relies on `digimoncard.io`'s free
   API. If that API goes down, card lookups will fail even though your
   bot is perfectly healthy — the `/health` check only reflects the
   Discord connection, not upstream API status, by design (so a flaky
   third-party API doesn't trigger unnecessary bot restarts).

## Security checklist

- [ ] **Never commit `.env`** — `.gitignore` and `.dockerignore` already
      exclude it. Double check with `git status` before your first commit.
- [ ] **Rotate the token if it ever leaks** — regenerate it instantly in
      the Discord Developer Portal (Bot tab → Reset Token) if it's ever
      exposed (committed to git, pasted in a public channel, etc.).
- [ ] **Minimum bot permissions** — when generating your OAuth2 invite
      URL, only grant `Send Messages` and `Embed Links`. Don't grant
      Administrator or anything you don't need.
- [ ] **Message Content Intent** is only needed for the `!card` prefix
      command. If you only plan to use `/card`, leave it disabled in the
      Developer Portal and remove `intents.message_content = True` from
      `bot.py` — fewer privileged intents means a smaller attack surface
      and no risk of the bot reading messages it doesn't need to.
- [ ] **Run as non-root** — both the Dockerfile and systemd unit already
      do this.
- [ ] **Keep dependencies updated** — periodically run
      `pip list --outdated` (or enable Dependabot/Renovate on the repo)
      and rebuild.
- [ ] **VPS-level hardening** (if using Option B on a fresh box):
      disable SSH password auth (key-only), run `ufw allow OpenSSH && ufw enable`,
      and consider `fail2ban` for brute-force protection.
- [ ] **Don't expose the healthcheck port publicly** — `docker-compose.yml`
      binds it to `127.0.0.1` only. If your uptime monitor runs on a
      different network than your bot, put a reverse proxy (nginx/Caddy)
      with basic auth in front of `/health` rather than opening the port
      to the whole internet.
