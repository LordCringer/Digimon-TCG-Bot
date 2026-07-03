# Digimon TCG Bot — Full Setup Guide

Three parts: create the Discord bot itself, install Ubuntu Server on your
spare PC, then deploy the bot on it. Follow in order — each part depends
on the one before it.

---

## Part 1 — Bot Setup (Discord Developer Portal)

### 1. Create the application and bot

1. Go to https://discord.com/developers/applications → **New Application**
   → give it a name (e.g. "Digimon Card Bot") → **Create**.
2. Left sidebar → **Bot** → **Add Bot** (or it may already exist).
3. Under **Token**, click **Reset Token** → **Copy**. Save this somewhere
   safe temporarily (a password manager, or a scratch file you'll delete
   after pasting it into `.env` later) — you can't view it again later,
   only regenerate it.

### 2. Set intents

Still on the **Bot** page, under **Privileged Gateway Intents**:
- **Message Content Intent** — only enable this if you want the `!card`
  prefix command to work. If you're only using `/card` (recommended),
  leave it **off** — fewer privileged intents means less attack surface.

### 3. Generate an invite link with minimal permissions

Left sidebar → **OAuth2** → **URL Generator**:
- **Scopes:** check `bot` and `applications.commands`
- **Bot Permissions:** check only `Send Messages` and `Embed Links` — no
  more than that
- Copy the generated URL at the bottom, open it in a browser, and select
  the server you want to add the bot to

### 4. Save the token for later

You'll paste this into a `.env` file during deployment (Part 3). Don't
paste it anywhere public — Discord chat, GitHub, screenshots, etc. If it
ever leaks, come back to this page and hit **Reset Token** immediately.

---

## Part 2 — Ubuntu Server Setup (on the spare PC)

### 1. Which version

**You've already installed Ubuntu Server 26.04 LTS on this machine** —
that's what the rest of this guide assumes. (For reference: 24.04 LTS
was the more battle-tested option since 26.04 is newer and had a couple
of installer crashes during setup, but once it's installed and running,
everything below works identically either way.)

### 2. Create the installer USB

- Download the **Ubuntu Server 24.04 LTS** ISO from ubuntu.com/download/server
- Write it to a USB drive (8 GB+) using **Rufus** (Windows), Balena
  Etcher, or `dd` (Mac/Linux)

### 3. Boot and install

1. Plug the USB into the spare PC, reboot, and enter the boot menu
   (usually F12 / F11 / Esc, varies by manufacturer) → select the USB
2. If it's not listed, disable **Secure Boot** in BIOS/UEFI first
3. Work through the installer:
   - **Network:** use **Ethernet**, not Wi-Fi, if at all possible — more
     reliable for an always-on box
   - **Storage:** "Use an entire disk" is simplest (wipes the drive —
     make sure nothing on it matters)
   - **Profile:** pick a username (this guide uses `botadmin`) and a
     strong password
   - **SSH setup:** check **"Install OpenSSH server"** — required, since
     you'll administer this headless from now on
   - **Featured server snaps:** skip all of these, including Docker —
     installed manually in Part 3 for more control
4. Reboot when it finishes, remove the USB

### 4. Its IP address

The server is already up at **`192.168.1.139`**. Make sure this is set
as a **DHCP reservation** / **static lease** in your router's admin page
(bound to the PC's MAC address, visible via `ip a` on the server, labeled
`link/ether`) so the address never changes and your SSH access doesn't
break later.

### 5. SSH in and lock it down

From your main computer:
```bash
ssh-copy-id botadmin@192.168.1.139
```
Then SSH in and disable password login:
```bash
ssh botadmin@192.168.1.139
sudo nano /etc/ssh/sshd_config
```
Set:
```
PasswordAuthentication no
```
```bash
sudo systemctl restart ssh
```

Firewall — the bot only makes outbound connections, so only SSH needs
to be allowed in:
```bash
sudo ufw allow OpenSSH
sudo ufw enable
```

### 6. BIOS and power settings

- Reboot into BIOS/UEFI setup (Del / F2 at boot) and find **"Restore on
  AC/Power Loss"** → set to **Power On**. Without this, a power blip
  leaves the machine off until someone presses the button.
- If you don't already have one, a cheap UPS ($50–80) protects against
  brief outages and lets you script a graceful shutdown for longer ones.

### 7. (Optional) Remote access without opening your router

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
Gives you a private, authenticated way to SSH in from anywhere without
forwarding any ports on your home router.

---

## Part 3 — Bot Deployment

### 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```
Log out and back in (`exit`, then SSH in again) for the group change to
take effect.

### 2. Add a deploy key and clone your private repo

```bash
ssh-keygen -t ed25519 -C "digimon-bot-deploy" -f ~/.ssh/deploy_key -N ""
cat ~/.ssh/deploy_key.pub
```
Copy the output → on GitHub: **LordCringer/Digimon-TCG-Bot → Settings →
Deploy keys → Add deploy key** → paste it. Read-only access is enough.

```bash
mkdir -p ~/apps && cd ~/apps
GIT_SSH_COMMAND="ssh -i ~/.ssh/deploy_key" git clone git@github.com:LordCringer/Digimon-TCG-Bot.git
cd Digimon-TCG-Bot
```

### 3. Configure the token

```bash
cp .env.example .env
nano .env
```
Paste the bot token from Part 1 as the value of `DISCORD_BOT_TOKEN`,
save (`Ctrl+O`, `Enter`, `Ctrl+X`), then lock down the file:
```bash
chmod 600 .env
```

### 4. Launch

```bash
docker compose up -d --build
docker compose logs -f
```
Watch for the "Logged in as ..." line, then `Ctrl+C` to stop watching
(the bot keeps running in the background).

Check it's healthy:
```bash
docker ps
```
Should show `healthy` in the status column once the `/health` check
passes (~20–30 seconds after startup).

### 5. Confirm it survives a reboot

```bash
sudo reboot
```
Wait ~30 seconds, SSH back in with `ssh botadmin@192.168.1.139`, and run
`docker ps` again — the container should already be back up. This works
because `restart: unless-stopped` in `docker-compose.yml` tells Docker
to restart the container whenever the Docker daemon starts, and the
Docker daemon itself starts automatically on boot via systemd.

### 6. Verify from another device on your network

```bash
curl http://192.168.1.139:8080/health
```
Should return `{"status": "ok", "latency_ms": ...}`.

### 7. Test it in Discord

In your server, type `/card name:` and start typing a Digimon name —
you should see live autocomplete suggestions with set numbers. Pick one
and confirm the card image posts.

---

## Ongoing maintenance

**SSH in:**
```bash
ssh botadmin@192.168.1.139
```

**Pulling updates:**
```bash
cd ~/apps/Digimon-TCG-Bot
GIT_SSH_COMMAND="ssh -i ~/.ssh/deploy_key" git pull
docker compose up -d --build
```

**Viewing logs:**
```bash
docker compose logs -f
```

**If the bot token ever leaks** (committed to git, pasted publicly,
etc.): go to the Discord Developer Portal → your app → **Bot** →
**Reset Token** immediately, then update `.env` on the server and
`docker compose up -d --build` again to pick up the new token.

**Security checklist recap:**
- [ ] `.env` never committed to git (`.gitignore` already covers this)
- [ ] Bot has only `Send Messages` + `Embed Links` permissions
- [ ] Message Content Intent left off unless you need `!card`
- [ ] SSH password login disabled, key-only
- [ ] UFW firewall enabled, only SSH allowed in
- [ ] Deploy key is read-only, not your personal GitHub credentials
- [ ] BIOS set to auto-power-on after outages
