#!/bin/bash
# APN Core Server - Production Setup Script
# Usage: sudo ./setup.sh [docker|systemd]

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

INSTALL_DIR="/opt/apn-core"
APN_USER="apn"
DEPLOY_MODE="${1:-systemd}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root: sudo $0"
    exit 1
fi

log_info "==================================================="
log_info "  APN Core Server - Production Setup"
log_info "  Deploy Mode: $DEPLOY_MODE"
log_info "==================================================="

# Create APN user if not exists
create_user() {
    if ! id "$APN_USER" &>/dev/null; then
        log_info "Creating user: $APN_USER"
        useradd -r -m -s /bin/bash "$APN_USER"
    else
        log_info "User $APN_USER already exists"
    fi
}

# Setup directories
setup_directories() {
    log_info "Setting up directories..."

    mkdir -p "$INSTALL_DIR"
    mkdir -p "/home/$APN_USER/.apn/logs"

    chown -R "$APN_USER:$APN_USER" "$INSTALL_DIR"
    chown -R "$APN_USER:$APN_USER" "/home/$APN_USER/.apn"
}

# Setup Python virtual environment
setup_venv() {
    log_info "Setting up Python virtual environment..."

    cd "$INSTALL_DIR"

    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi

    source venv/bin/activate
    pip install --upgrade pip wheel
    pip install -r requirements.txt
    deactivate

    chown -R "$APN_USER:$APN_USER" "$INSTALL_DIR/venv"
}

# Copy application files
copy_files() {
    log_info "Copying application files..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    APP_DIR="$(dirname "$SCRIPT_DIR")"

    cp -r "$APP_DIR"/*.py "$INSTALL_DIR/"
    cp -r "$APP_DIR/core" "$INSTALL_DIR/"
    cp -r "$APP_DIR/app" "$INSTALL_DIR/" 2>/dev/null || true
    cp "$APP_DIR/requirements.txt" "$INSTALL_DIR/"

    # Create .env from example if not exists
    if [ ! -f "$INSTALL_DIR/.env" ] && [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$INSTALL_DIR/.env"
        log_warn "Created .env from template - please configure it!"
    fi

    chown -R "$APN_USER:$APN_USER" "$INSTALL_DIR"
}

# Generate API key
generate_api_key() {
    if grep -q "^APN_API_KEY=$" "$INSTALL_DIR/.env" 2>/dev/null; then
        API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
        sed -i "s/^APN_API_KEY=$/APN_API_KEY=$API_KEY/" "$INSTALL_DIR/.env"
        log_info "Generated API key: $API_KEY"
        log_warn "Save this API key - you'll need it to authenticate requests!"
    fi
}

# Setup systemd service
setup_systemd() {
    log_info "Setting up systemd service..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    cp "$SCRIPT_DIR/apn-core.service" /etc/systemd/system/

    systemctl daemon-reload
    systemctl enable apn-core

    log_info "Systemd service installed"
    log_info "  Start: sudo systemctl start apn-core"
    log_info "  Status: sudo systemctl status apn-core"
    log_info "  Logs: sudo journalctl -u apn-core -f"
}

# Setup Docker
setup_docker() {
    log_info "Setting up Docker deployment..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    APP_DIR="$(dirname "$SCRIPT_DIR")"

    cd "$APP_DIR"

    # Build image
    docker build -t apn-core:latest .

    log_info "Docker image built: apn-core:latest"
    log_info "  Run: docker-compose up -d"
    log_info "  Logs: docker-compose logs -f"
}

# Setup nginx (optional)
setup_nginx() {
    if command -v nginx &>/dev/null; then
        log_info "Setting up nginx reverse proxy..."

        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

        if [ -f "$SCRIPT_DIR/nginx.conf" ]; then
            cp "$SCRIPT_DIR/nginx.conf" /etc/nginx/sites-available/apn-core

            log_warn "Please edit /etc/nginx/sites-available/apn-core to configure your domain"
            log_info "Then run: sudo ln -s /etc/nginx/sites-available/apn-core /etc/nginx/sites-enabled/"
            log_info "And: sudo nginx -t && sudo systemctl reload nginx"
        fi
    else
        log_warn "nginx not installed - skipping reverse proxy setup"
    fi
}

# Main installation
main() {
    create_user
    setup_directories
    copy_files

    if [ "$DEPLOY_MODE" = "docker" ]; then
        setup_docker
    else
        setup_venv
        generate_api_key
        setup_systemd
        setup_nginx
    fi

    log_info ""
    log_info "==================================================="
    log_info "  APN Core Server - Installation Complete!"
    log_info "==================================================="
    log_info ""
    log_info "Configuration file: $INSTALL_DIR/.env"
    log_info "Data directory: /home/$APN_USER/.apn/"
    log_info "Log directory: /home/$APN_USER/.apn/logs/"
    log_info ""

    if [ "$DEPLOY_MODE" = "docker" ]; then
        log_info "Start with: cd $(dirname "$SCRIPT_DIR") && docker-compose up -d"
    else
        log_info "Start with: sudo systemctl start apn-core"
    fi

    log_info ""
}

main
