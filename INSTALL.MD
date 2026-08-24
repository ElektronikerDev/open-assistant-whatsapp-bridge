# Installation

This guide installs the Open Assistant WhatsApp Bridge on a Debian/Ubuntu Linux server and runs it as a `systemd` service.

The bridge connects to WhatsApp through **WhatsApp Web / Neonize**. On the first start, a **QR code is displayed directly in the console**. Scan this QR code with WhatsApp to pair the server. No Meta Developer webhook or WhatsApp Cloud API credentials are required.

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

## 4. Initial configuration

Run the interactive setup once:

```bash
python3 whatsapp_openassistant_bridge.py --init
```

The setup asks for:

- the allowed WhatsApp number, without `+`
- the Open Assistant chat URL
- the Open Assistant upload URL
- an optional Open Assistant bearer token
- the Open Assistant channel

The configuration is stored in `.env` with restricted permissions.

Example values:

```env
ALLOWED_WHATSAPP_NUMBER=491234567890
OPENASSISTANT_URL=http://127.0.0.1:8080/api/chat/stream
OPENASSISTANT_UPLOAD_URL=http://127.0.0.1:8080/api/upload
OPENASSISTANT_TOKEN=
OPENASSISTANT_CHANNEL=whatsapp

HOST=127.0.0.1
PORT=8081
```

The setup also creates a random `BRIDGE_TOKEN` if none exists yet.

## 5. First start and WhatsApp QR pairing

Start the bridge manually before creating the service:

```bash
source .venv/bin/activate
python3 whatsapp_openassistant_bridge.py
```

If no saved WhatsApp session exists yet, the bridge starts QR pairing and prints a QR code directly in the terminal.

![WhatsApp QR code shown in the console](https://image.cod3.eu/f2bZuRRTKS)

On your phone, open WhatsApp and go to:

**Settings → Linked devices → Link a device**

Then scan the QR code shown in the terminal.

After a successful connection, the bridge prints information similar to:

```text
✓ WhatsApp connected
✓ Allowed number: ...
✓ Open Assistant: ...
✓ Upload API: ...
```

The WhatsApp session is stored in the configured database and reused on later starts. A new QR code is therefore normally only required when no stored session exists or the WhatsApp session has been invalidated.

Stop the manual test with:

```text
Ctrl+C
```

## 6. Create a systemd service

Create the service file:

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
ExecStart=/opt/open-assistant-whatsapp-bridge/.venv/bin/python /opt/open-assistant-whatsapp-bridge/whatsapp_openassistant_bridge.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Give the service user access to the application directory, including the WhatsApp session database:

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

## 7. Check status and logs

Check the service status:

```bash
sudo systemctl status open-assistant-whatsapp
```

Follow the logs:

```bash
sudo journalctl -u open-assistant-whatsapp -f
```

Restart after configuration changes:

```bash
sudo systemctl restart open-assistant-whatsapp
```

## 8. Bridge HTTP API

The bridge also starts a small HTTP server. By default it listens on:

```text
127.0.0.1:8081
```

Health check:

```bash
curl http://127.0.0.1:8081/health
```

The `/send` endpoint can be used to send a WhatsApp message through the paired session. If `BRIDGE_TOKEN` is configured, authenticate with:

```text
Authorization: Bearer YOUR_BRIDGE_TOKEN
```

By default, sending is restricted to `ALLOWED_WHATSAPP_NUMBER` unless `ALLOW_SEND_TO_ANY=true` is explicitly configured.

## 9. Conversation behavior

The bridge keeps a conversation ID for the current day.

- `/clear` starts a new conversation.
- A new day automatically starts a new conversation.
- Incoming group messages are ignored by default.
- Images are stored only temporarily and deleted locally after processing.

## Update

To update the bridge later:

```bash
cd /opt/open-assistant-whatsapp-bridge
sudo -u www-data git pull
sudo -u www-data .venv/bin/pip install -r requirements.txt
sudo systemctl restart open-assistant-whatsapp
```

If an update changes the setup variables, run the interactive setup again if needed:

```bash
sudo -u www-data /opt/open-assistant-whatsapp-bridge/.venv/bin/python \
  /opt/open-assistant-whatsapp-bridge/whatsapp_openassistant_bridge.py --init
```
