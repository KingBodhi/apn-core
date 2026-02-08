#!/bin/bash
# APN Core Connection Diagnostic Script
# Run this on the device that won't connect

echo "======================================"
echo "APN CORE CONNECTION DIAGNOSTIC"
echo "======================================"
echo ""

# 1. Check if in apn-core directory
echo "1. Current directory:"
pwd
echo ""

# 2. Check if apn-core exists
echo "2. APN Core repository status:"
if [ -d ".git" ]; then
    echo "✓ Git repository found"
    git status | head -3
else
    echo "✗ Not in APN Core directory!"
fi
echo ""

# 3. Check Python version
echo "3. Python version:"
python3 --version 2>&1
python3.10 --version 2>&1 || echo "python3.10 not found"
echo ""

# 4. Check if dependencies are installed
echo "4. Critical dependencies:"
python3 -c "import PyQt6; print('✓ PyQt6 installed')" 2>&1 || echo "✗ PyQt6 NOT installed"
python3 -c "import nats; print('✓ nats-py installed')" 2>&1 || echo "✗ nats-py NOT installed"
python3 -c "import fastapi; print('✓ fastapi installed')" 2>&1 || echo "✗ fastapi NOT installed"
echo ""

# 5. Check if APN Core server is running
echo "5. APN Core server status (port 8000):"
if lsof -i :8000 >/dev/null 2>&1; then
    echo "✓ Server is RUNNING on port 8000"
    lsof -i :8000 | grep LISTEN
else
    echo "✗ Server NOT running on port 8000"
fi
echo ""

# 6. Check server health endpoint
echo "6. Server health check:"
curl -s http://localhost:8000/health 2>&1 | head -3 || echo "✗ Cannot connect to server"
echo ""

# 7. Check node identity
echo "7. Node identity:"
if [ -f "$HOME/.apn/node_identity.json" ]; then
    echo "✓ Identity file exists"
    echo "Node ID: $(cat ~/.apn/node_identity.json | python3 -c "import sys, json; print(json.load(sys.stdin).get('node_id', 'N/A'))" 2>/dev/null)"
    echo "Wallet: $(cat ~/.apn/node_identity.json | python3 -c "import sys, json; print(json.load(sys.stdin).get('payment_address', 'N/A'))" 2>/dev/null)"
else
    echo "✗ Identity file NOT found at ~/.apn/node_identity.json"
fi
echo ""

# 8. Check contribution settings
echo "8. Contribution settings:"
if [ -f "$HOME/.apn/contribution_settings.json" ]; then
    echo "✓ Contribution settings exist"
    cat ~/.apn/contribution_settings.json
else
    echo "✗ Contribution settings NOT found"
    echo "  Contribution may be disabled!"
fi
echo ""

# 9. Test NATS connectivity
echo "9. NATS relay connectivity test:"
timeout 3 bash -c "echo 'PING' | nc nonlocal.info 4222" >/dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "✓ Can connect to NATS relay (nonlocal.info:4222)"
else
    echo "✗ CANNOT connect to NATS relay"
    echo "  Check firewall/network!"
fi
echo ""

# 10. Check recent logs
echo "10. Recent APN logs (last 20 lines):"
if [ -f "$HOME/.apn/apn.log" ]; then
    tail -20 ~/.apn/apn.log
else
    echo "✗ No log file found at ~/.apn/apn.log"
fi
echo ""

# 11. Check for running processes
echo "11. APN-related processes:"
ps aux | grep -E "python.*main.py|python.*apn_server|uvicorn.*apn_server" | grep -v grep || echo "No APN processes found"
echo ""

# 12. System info
echo "12. System information:"
echo "Hostname: $(hostname)"
echo "CPU cores: $(nproc)"
echo "RAM: $(free -h | awk '/^Mem:/ {print $2}')"
echo "GPU: $(lspci | grep -i vga | head -1)"
echo "NVIDIA GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'Not detected')"
echo ""

echo "======================================"
echo "DIAGNOSTIC COMPLETE"
echo "======================================"
echo ""
echo "Quick fixes:"
echo "  - If dependencies missing: pip install -r requirements.txt"
echo "  - If server not running: ./launch.sh"
echo "  - If contribution disabled: Enable in GUI or edit ~/.apn/contribution_settings.json"
echo "  - If NATS unreachable: Check firewall, try: telnet nonlocal.info 4222"
