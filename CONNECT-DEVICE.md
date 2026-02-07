# APN Core - Connect Your Device to Earn VIBE Rewards

## 🚀 Quick Start

Get your device connected to the Alpha Protocol Network in 3 steps:

### 1. Install Dependencies

```bash
cd apn-core
pip install -r requirements.txt
```

**New Requirement:** Make sure `nats-py` is installed for network connectivity:
```bash
pip install nats-py>=2.6.0
```

### 2. Run APN Core

**Option A - Using launcher (recommended):**
```bash
./launch.sh
```

**Option B - Direct Python:**
```bash
python3.10 main.py
```

The modern GUI will open automatically, displaying your wallet address prominently.

### 3. Enable Device Contribution

The new modern UI features a clean card-based design:

1. **Wallet Card** - Your unique wallet address is displayed prominently at the top
2. **Network Status Card** - Shows connection status to NATS relay
3. **Contribution Card** - Click **"Enable Contribution"** button
4. **Resources Card** - Displays your system's CPU, RAM, Storage, GPU

Once enabled, your device will:
- Connect to NATS relay: `nats://nonlocal.info:4222`
- Send heartbeats every 30 seconds
- Earn VIBE rewards automatically!
- Show live status updates every 5 seconds

---

## 💰 VIBE Rewards System

### How It Works

**Automatic Earnings:** Your device earns VIBE tokens just for being online and contributing resources.

**Reward Formula:**
- **Base:** 0.1 VIBE per heartbeat (every 30 seconds)
- **GPU Multiplier:** 2x (if GPU detected)
- **High CPU Multiplier:** 1.5x (if >16 cores)
- **High RAM Multiplier:** 1.3x (if >32GB RAM)

### Expected Earnings

**Example configurations:**

| Device Type | CPU | RAM | GPU | Per Heartbeat | Per Hour | Per Day |
|-------------|-----|-----|-----|---------------|----------|---------|
| Basic laptop | 8 cores | 16GB | No | 0.1 VIBE | 12 VIBE | 288 VIBE |
| Gaming PC | 16 cores | 32GB | RTX 3060 | 0.2 VIBE | 24 VIBE | 576 VIBE |
| Workstation | 24 cores | 64GB | RTX 3090 | 0.39 VIBE | 47 VIBE | 1,128 VIBE |
| Mac Studio | 20 cores | 64GB | No | 0.195 VIBE | 23 VIBE | 562 VIBE |

**Multiplier Calculation:**
```
Final Reward = Base × GPU Multiplier × CPU Multiplier × RAM Multiplier
```

### Automatic Distribution

- Rewards are tracked automatically on the Pythia master node
- Distributions sent to your wallet every 5 minutes
- On-chain via Aptos blockchain (Testnet)

---

## 🔍 Check Your Rewards

### Get Your Wallet Address

Your wallet address is displayed in the APN Core GUI after starting contribution.

Or check the config file:
```bash
cat ~/.apn/node_config.json | grep paymentAddress
```

### Check Balance (API)

```bash
# Replace YOUR_WALLET with your actual address
curl http://192.168.1.77:8081/api/peers/YOUR_WALLET/balance | jq
```

**Example response:**
```json
{
  "wallet_address": "0x...",
  "balance_vibe": "156.8",
  "pending_distribution_vibe": "12.3",
  "total_earned_vibe": "169.1"
}
```

### View Reward History

```bash
curl "http://192.168.1.77:8081/api/peers/YOUR_WALLET/rewards?limit=10" | jq
```

### Network Stats

```bash
curl http://192.168.1.77:8081/api/network/stats | jq
```

---

## 📊 Verify Connection

### Check Heartbeat Status

After starting contribution, verify your device is sending heartbeats:

1. **GUI Status:** Node Config tab should show "Contributing: Yes"
2. **Log File:** Check `~/.apn/apn.log` for heartbeat messages
3. **Network:** Should appear in Pythia's connected peers

### From Pythia Master Node

If you have access to the master node (192.168.1.77), verify:

```bash
# Check if your node appears in database
sqlite3 ~/pcg-cc-mcp/dev_assets/db.sqlite "SELECT node_id, wallet_address, cpu_cores, ram_mb, gpu_available FROM peer_nodes WHERE is_active = 1;"

# Watch heartbeat logs
tail -f /tmp/apn_node.log | grep "📨 Message from apn.heartbeat"
```

---

## 🐛 Troubleshooting

### Can't Connect to NATS

**Error:** "Failed to connect to NATS relay"

**Solution:**
```bash
# Test connectivity
telnet nonlocal.info 4222

# If fails, check internet connection and firewall
```

### No Rewards Appearing

**Possible causes:**
1. Node not sending heartbeats
2. Wallet address not configured
3. Network connectivity issues

**Solutions:**
```bash
# Check logs
tail -50 ~/.apn/apn.log | grep -i heartbeat

# Verify NATS dependency
pip install --upgrade nats-py

# Restart APN Core
python main.py
```

### GUI Won't Start

**Solution:**
```bash
# Install GUI dependencies
pip install PyQt6 PyQt6-WebEngine

# If on Linux without display
export DISPLAY=:0
python main.py
```

---

## 🌐 Network Information

**Pythia Master Node:**
- IP: 192.168.1.77
- NATS Relay: nats://nonlocal.info:4222
- API: http://192.168.1.77:8081/api/
- Dashboard: http://192.168.1.77:8081/

**Network Status:**
- Master node ID: `apn_814d37f4`
- Protocol Version: alpha/1.0.0
- Reward Distribution: Every 5 minutes
- Heartbeat Interval: 30 seconds

---

## 🔐 Security Notes

**What's Shared:**
- CPU core count (not usage)
- Total RAM (not process details)
- Available storage (not file contents)
- GPU model (not usage data)

**What's NOT Shared:**
- Your files or data
- Process information
- Network traffic contents
- Personal information

All communication is encrypted via the APN secure channel protocol.

---

## 📞 Support

**Connection Issues:**
- Verify NATS relay: `telnet nonlocal.info 4222`
- Check dependencies: `pip list | grep nats`
- Review logs: `~/.apn/apn.log`

**Reward Issues:**
- Verify wallet address is set
- Check API for balance (see above)
- Ensure heartbeats are sending

**Other Issues:**
- Check system requirements (Python 3.10+)
- Update dependencies: `pip install -r requirements.txt --upgrade`
- Review documentation: `README.md`

---

**Version:** 1.0.0
**Last Updated:** 2026-02-06
**Status:** Production Ready ✅
**Rewards:** ACTIVE - Start earning VIBE now! 💰
