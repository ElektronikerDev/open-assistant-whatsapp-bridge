# Installation

This guide installs the Open Assistant WhatsApp Bridge on a Debian/Ubuntu Linux server and runs it as a `systemd` service.

## 1. Install dependencies

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv
```

## 2. Clone the repository

```bash
cd /opt
sudo git clone https://github.com/ElektronikerDev/open-assistant-whatsapp-bridge.git
sudo chown -R $USER:$USER /opt/open-assistant-whatsapp-bridge
cd /opt/open-assistant-whatsapp-bridge
```

## 3. Create Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the Python dependencies:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Configure the bridge

Create the environment file:

```bash
nano .env
```

Example:

```env
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=
META_APP_SECRET=

OPEN_ASSISTANT_URL=
OPEN_ASSISTANT_API_KEY=

PORT=3000
```

Fill in your WhatsApp and Open Assistant credentials.

Protect the file:

```bash
chmod 600 .env
```

## 5. Test the application

Start the bridge manually first:

```bash
source .venv/bin/activate
python3 main.py
```

If the application starts correctly, stop it with:

```text
Ctrl+C
```

## 6. Create a systemd service

Create:

```bash
sudo nano /etc/systemd/system/open-assistant-whatsapp.service
```

Add:

```ini
[Unit]
Description=Open Assistant WhatsApp Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/open-assistant-whatsapp-bridge
EnvironmentFile=/opt/open-assistant-whatsapp-bridge/.env
ExecStart=/opt/open-assistant-whatsapp-bridge/.venv/bin/python /opt/open-assistant-whatsapp-bridge/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Set ownership:

```bash
sudo chown -R www-data:www-data /opt/open-assistant-whatsapp-bridge
```

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Enable the service:

```bash
sudo systemctl enable open-assistant-whatsapp
```

Start it:

```bash
sudo systemctl start open-assistant-whatsapp
```

## 7. Check status

```bash
sudo systemctl status open-assistant-whatsapp
```

View logs:

```bash
sudo journalctl -u open-assistant-whatsapp -f
```

Restart after configuration changes:

```bash
sudo systemctl restart open-assistant-whatsapp
```

## 8. Configure WhatsApp webhook

In the Meta Developer Dashboard configure the webhook URL:

```text
https://your-domain.example/webhook
```

Use the same verification token configured in:

```env
WHATSAPP_VERIFY_TOKEN=
```

Subscribe the webhook to WhatsApp message events.

## Update

To update the bridge later:

```bash
cd /opt/open-assistant-whatsapp-bridge
sudo -u www-data git pull
sudo -u www-data .venv/bin/pip install -r requirements.txt
sudo systemctl restart open-assistant-whatsapp
```