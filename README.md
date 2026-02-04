# APN Core Dashboard v1.0.0

**Alpha Protocol Network - Sovereign Mesh Networking**

A PyQt6-based dashboard for managing APN Core nodes, enabling devices to contribute resources to the Alpha Protocol Network mesh.

## Features

- **Mesh Networking**: Connect to the APN mesh via NATS relay
- **Device Contribution**: Contribute CPU, storage, and bandwidth to the network
- **Wearable Integration**: Connect rings and glasses via USB bridge
- **Node Configuration**: Configure relay, storage, compute, and bridge services
- **System Monitoring**: Real-time CPU, RAM, storage, and GPU monitoring
- **Meshtastic Support**: Long-range radio mesh networking

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the dashboard
python main.py
```

The APN Core server will start automatically on port 8000.

## Architecture

```
alpha-dashboard/
├── main.py                 # Application entry point
├── apn_server.py           # APN Core FastAPI server
├── core/
│   ├── config.py           # Configuration management
│   ├── web_server.py       # Web server implementation
│   ├── service_manager.py  # Service orchestration
│   └── ...
├── app/
│   ├── main_window.py      # Main PyQt6 window
│   └── pages/
│       ├── home_page.py    # Dashboard home
│       ├── apn_page.py     # Node configuration & contribution
│       ├── devices_page.py # Device management
│       └── ...
└── scripts/
    └── setup_android_bridge.sh  # Android bridge setup
```

## API Endpoints

The APN Core server provides:

- `GET /` - Landing page
- `GET /health` - Health check
- `GET /api/version` - Version information
- `GET /api/resources` - System resources
- `GET /api/contribution/status` - Contribution status
- `POST /api/contribution/settings` - Update contribution settings
- `GET /api/mesh/peers` - Connected mesh peers
- `POST /register` - Register peer node
- `WebSocket /api/events/ws` - Real-time events

## Device Contribution

Enable device contribution to earn rewards:

1. Open the **Node Config** page
2. Check "Enable Device Contribution"
3. Select services to contribute (Relay, Compute, Storage)
4. Click "Start Contributing"

Your node will connect to the Pythia master node at `nats://nonlocal.info:4222`.

## Configuration

Configuration is stored in `~/.apn/`:

- `apn_config.json` - Main node configuration
- `node_config.json` - Node identity and roles
- `contribution_settings.json` - Contribution settings
- `node.key` - Node private key (Ed25519)

## Default Peers

- `https://dashboard.powerclubglobal.com`
- `https://pythia.nonlocal.info`
- NATS Relay: `nats://nonlocal.info:4222`

## Requirements

- Python 3.10+
- PyQt6
- FastAPI
- psutil (for system monitoring)

See `requirements.txt` for full dependencies.

## License

Part of the Alpha Protocol Network project.
