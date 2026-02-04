#!/bin/bash
# APN Core v1.0.0 - Android Bridge Setup
# Sets up USB connection between Android device and APN CORE

set -e

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           APN CORE v1.0.0 - Android Bridge Setup              ║"
echo "║     Alpha Protocol Network - Sovereign Mesh Networking       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

DASHBOARD_PORT=8000

# Check if ADB is installed
check_adb() {
    echo -e "${BLUE}[1/5]${NC} Checking ADB installation..."
    if command -v adb &> /dev/null; then
        ADB_VERSION=$(adb version | head -1)
        echo -e "${GREEN}✓${NC} ADB found: $ADB_VERSION"
        return 0
    else
        echo -e "${RED}✗${NC} ADB not found"
        return 1
    fi
}

# Install ADB
install_adb() {
    echo -e "${YELLOW}Installing ADB...${NC}"
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y adb android-tools-adb
    elif command -v pacman &> /dev/null; then
        sudo pacman -S android-tools
    elif command -v dnf &> /dev/null; then
        sudo dnf install android-tools
    elif command -v brew &> /dev/null; then
        brew install android-platform-tools
    else
        echo -e "${RED}Could not determine package manager. Please install ADB manually.${NC}"
        exit 1
    fi
}

# Start ADB server
start_adb_server() {
    echo -e "${BLUE}[2/5]${NC} Starting ADB server..."
    adb start-server 2>/dev/null || true
    echo -e "${GREEN}✓${NC} ADB server started"
}

# Check for connected devices
check_devices() {
    echo -e "${BLUE}[3/5]${NC} Scanning for Android devices..."
    echo ""

    DEVICES=$(adb devices -l 2>/dev/null | tail -n +2 | grep -v "^$")

    if [ -z "$DEVICES" ]; then
        echo -e "${YELLOW}No Android devices found.${NC}"
        echo ""
        echo "Please ensure:"
        echo "  1. Your Android device is connected via USB"
        echo "  2. USB Debugging is enabled in Developer Options"
        echo "  3. You've authorized this computer on your device"
        echo ""
        echo "To enable USB Debugging:"
        echo "  Settings → About Phone → Tap 'Build Number' 7 times"
        echo "  Settings → Developer Options → Enable USB Debugging"
        echo ""
        return 1
    fi

    echo "Found devices:"
    echo "$DEVICES" | while read line; do
        if [ -n "$line" ]; then
            SERIAL=$(echo "$line" | awk '{print $1}')
            MODEL=$(echo "$line" | grep -oP 'model:\K[^ ]+' || echo "Unknown")
            STATUS=$(echo "$line" | awk '{print $2}')
            echo -e "  ${GREEN}•${NC} $MODEL ($SERIAL) - $STATUS"
        fi
    done
    echo ""
    return 0
}

# Setup port forwarding
setup_port_forward() {
    echo -e "${BLUE}[4/5]${NC} Setting up port forwarding..."

    # Get first connected device
    DEVICE=$(adb devices | tail -n +2 | grep "device$" | head -1 | awk '{print $1}')

    if [ -z "$DEVICE" ]; then
        echo -e "${RED}✗${NC} No authorized device found"
        return 1
    fi

    # Setup reverse port forwarding
    # This makes localhost:8000 on the phone point to localhost:8000 on the computer
    adb -s "$DEVICE" reverse tcp:$DASHBOARD_PORT tcp:$DASHBOARD_PORT

    echo -e "${GREEN}✓${NC} Port forwarding configured"
    echo "   Phone localhost:$DASHBOARD_PORT → Computer localhost:$DASHBOARD_PORT"
}

# Test connection
test_connection() {
    echo -e "${BLUE}[5/5]${NC} Testing connection..."

    # Check if dashboard is running
    if curl -s "http://localhost:$DASHBOARD_PORT/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Dashboard is reachable"
    else
        echo -e "${YELLOW}!${NC} Dashboard is not running on port $DASHBOARD_PORT"
        echo "   Start the dashboard first, then the companion app can connect"
    fi
}

# Print final instructions
print_instructions() {
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo -e "${GREEN}APN CORE Setup Complete!${NC}"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "Next steps:"
    echo "  1. Start APN CORE:"
    echo "     cd apn-core && python main.py"
    echo ""
    echo "  2. Open the Wearables Companion app on your phone"
    echo ""
    echo "  3. In the app settings, set Dashboard URL to:"
    echo -e "     ${BLUE}http://localhost:$DASHBOARD_PORT${NC}"
    echo ""
    echo "  4. Connect your ring and glasses in the app"
    echo ""
    echo "  5. Events will flow through USB to APN CORE!"
    echo ""
    echo "  6. Enable device contribution in the Node Config page"
    echo "     to start earning rewards on the mesh network!"
    echo ""
    echo "To manually refresh port forwarding:"
    echo "  adb reverse tcp:$DASHBOARD_PORT tcp:$DASHBOARD_PORT"
    echo ""
}

# Main execution
main() {
    # Check/Install ADB
    if ! check_adb; then
        echo ""
        read -p "Install ADB now? [Y/n] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
            install_adb
            check_adb || exit 1
        else
            echo "Please install ADB manually and run this script again."
            exit 1
        fi
    fi

    start_adb_server
    echo ""

    if check_devices; then
        setup_port_forward
        test_connection
        print_instructions
    else
        echo ""
        echo "Connect your Android device and run this script again."
        exit 1
    fi
}

main "$@"
