#!/bin/bash
# =============================================================================
# Sekisho Sync — Uninstaller v1.2.2
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

INSTALL_LIB="/usr/lib/sekisho-sync"
INSTALL_BIN="/usr/local/bin"
DATA_DIR="/var/lib/sekisho"
CONF_DIR="/etc/sekisho-sync"
SYSTEMD_DIR="/etc/systemd/system"
LOGROTATE_DIR="/etc/logrotate.d"
SEKISHO_USER="sekisho"

print_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "Requires root. Run: sudo $0"
        exit 1
    fi
}

confirm() {
    echo ""
    echo "=========================================="
    echo "  SEKISHO SYNC — UNINSTALL v1.2.2"
    echo "=========================================="
    echo ""
    print_warning "This will remove:"
    echo "  - systemd services (sekisho-sync, sekisho-viewer)"
    echo "  - Installed files in $INSTALL_LIB"
    echo "  - CLI tool: palm_memo_extract"
    echo "  - Config: $CONF_DIR"
    echo "  - Logrotate config"
    echo ""
    read -p "Also remove backup data in $DATA_DIR? [y/N]: " -n 1 -r
    echo
    [[ $REPLY =~ ^[YySs]$ ]] && REMOVE_DATA=true || REMOVE_DATA=false

    echo ""
    read -p "Continue with uninstall? [y/N]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[YySs]$ ]]; then
        print_info "Cancelled."
        exit 0
    fi
}

stop_services() {
    for svc in sekisho-sync sekisho-viewer; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            print_info "Stopping $svc..."
            systemctl stop "$svc" || true
        fi
        if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
            systemctl disable "$svc" || true
        fi
        [[ -f "$SYSTEMD_DIR/$svc.service" ]] && rm -f "$SYSTEMD_DIR/$svc.service"
    done
    systemctl daemon-reload 2>/dev/null || true
    print_success "Services removed"
}

remove_files() {
    [[ -d "/opt/sekisho-sync" ]] && rm -rf "/opt/sekisho-sync" && print_success "Removed /opt/sekisho-sync (lib + venv + vendored binaries)"
    [[ -L "$INSTALL_BIN/palm_memo_extract" ]] && rm -f "$INSTALL_BIN/palm_memo_extract" \
        && print_success "Removed CLI symlink"
    [[ -f "$INSTALL_BIN/pilot-xfer" ]] && rm -f "$INSTALL_BIN/pilot-xfer" \
        && print_success "Removed pilot-xfer wrapper"
    [[ -d "$CONF_DIR" ]] && rm -rf "$CONF_DIR" && print_success "Removed $CONF_DIR"
    [[ -f "$LOGROTATE_DIR/sekisho-sync" ]] && rm -f "$LOGROTATE_DIR/sekisho-sync" \
        && print_success "Removed logrotate config"
}

remove_data() {
    if [[ "$REMOVE_DATA" == true ]]; then
        [[ -d "$DATA_DIR" ]] && rm -rf "$DATA_DIR" && print_success "Removed $DATA_DIR"
    else
        print_warning "Data preserved at: $DATA_DIR"
        echo "  To remove manually: sudo rm -rf $DATA_DIR"
    fi
}

remove_user() {
    if id "$SEKISHO_USER" &>/dev/null; then
        read -p "Remove system user '$SEKISHO_USER'? [y/N]: " -n 1 -r
        echo
        if [[ $REPLY =~ ^[YySs]$ ]]; then
            userdel "$SEKISHO_USER" 2>/dev/null || true
            print_success "User removed"
        fi
    fi
}

main() {
    check_root
    confirm
    stop_services
    remove_files
    remove_data
    remove_user
    echo ""
    echo -e "${GREEN}Uninstall complete.${NC}"
}

main "$@"
