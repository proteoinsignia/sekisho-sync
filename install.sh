#!/bin/bash
# =============================================================================
# Sekisho Sync — Installer v1.2.2
#
# Usage: sudo ./install.sh [--no-create-user]
#   --no-create-user   Run daemons as root (not recommended)
#
# Requirements: python3 with venv module, systemd
# pilot-link is vendored automatically — not a system dependency.
# No apt/dnf/pacman dependency for Python packages — all installed in venv.
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/src"

INSTALL_LIB="/opt/sekisho-sync/lib"
PILOT_BIN_DIR="/opt/sekisho-sync/bin"
VENV_DIR="/opt/sekisho-sync/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
INSTALL_BIN="/usr/local/bin"
DATA_DIR="/var/lib/sekisho"
CONF_DIR="/etc/sekisho-sync"
SYSTEMD_DIR="/etc/systemd/system"
LOGROTATE_DIR="/etc/logrotate.d"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"

SYNC_SERVICE="sekisho-sync"
VIEWER_SERVICE="sekisho-viewer"
NETSYNC_PORT=14238
DEFAULT_PORT=5000

CREATE_USER=true
SEKISHO_USER="sekisho"

# =============================================================================
print_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[OK]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

parse_args() {
    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --no-create-user) CREATE_USER=false ;;
            --help|-h)
                echo "Usage: sudo $0 [--no-create-user]"
                echo "  --no-create-user   Run daemons as root (not recommended)"
                exit 0 ;;
            *) print_error "Unknown argument: $1"; exit 1 ;;
        esac
        shift
    done
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "Requires root. Run: sudo $0"
        exit 1
    fi
}

confirm_root_execution() {
    if [[ "$CREATE_USER" == false ]]; then
        echo ""
        print_warning "============================================================"
        print_warning "  SECURITY WARNING: daemons will run as ROOT"
        print_warning "  This weakens systemd hardening significantly."
        print_warning "============================================================"
        echo ""
        read -p "Continue without dedicated user? [y/N]: " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[YySs]$ ]]; then
            print_info "Aborted."
            exit 0
        fi
    fi
}

check_arch() {
    local arch
    arch=$(uname -m)
    if [[ "$arch" != "aarch64" ]]; then
        print_error "Unsupported architecture: $arch"
        echo "  This installer currently supports ARM64 systems only."
        exit 1
    fi
    print_success "Architecture: $arch"
}

check_dependencies() {
    print_info "Checking system dependencies..."

    # python3
    if ! command -v python3 &>/dev/null; then
        print_error "python3 not found."
        echo "  Install: apt install python3"
        exit 1
    fi

    # Flask 3.x requires Python 3.8+
    local py_version
    py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    local py_major py_minor
    py_major=$(python3 -c "import sys; print(sys.version_info.major)")
    py_minor=$(python3 -c "import sys; print(sys.version_info.minor)")

    if [[ "$py_major" -lt 3 ]] || [[ "$py_major" -eq 3 && "$py_minor" -lt 8 ]]; then
        print_error "Python $py_version found — Python 3.8+ required (Flask 3.x dependency)."
        echo "  Current: $py_version"
        echo "  Minimum: 3.8"
        exit 1
    fi
    print_info "python3: $(command -v python3) ($py_version)"

    # python3 venv module
    if ! python3 -c "import venv" 2>/dev/null; then
        print_error "python3 venv module not available."
        echo "  Install: apt install python3-venv"
        exit 1
    fi
    print_success "python3-venv: OK"

    # systemd
    if ! command -v systemctl &>/dev/null; then
        print_error "systemd not found. This installer requires systemd."
        exit 1
    fi

    # requirements.txt present
    if [[ ! -f "$REQUIREMENTS" ]]; then
        print_error "requirements.txt not found at $REQUIREMENTS"
        exit 1
    fi
    print_success "requirements.txt: found"

    # source files
    for f in config.py sekisho_sync.py palm_memo_extract.py palm_memo_viewer.py; do
        if [[ ! -f "$SRC_DIR/$f" ]]; then
            print_error "Missing source file: $SRC_DIR/$f"
            exit 1
        fi
    done
    print_success "Source files: OK"
}

check_ports() {
    print_info "Checking ports..."
    for port in $NETSYNC_PORT $DEFAULT_PORT; do
        if command -v ss &>/dev/null; then
            if ss -tlnp 2>/dev/null | grep -q ":$port "; then
                print_warning "Port $port already in use"
            else
                print_success "Port $port available"
            fi
        fi
    done
}

check_existing() {
    print_info "Checking existing installation..."
    local updating=false
    for svc in "$SYNC_SERVICE" "$VIEWER_SERVICE"; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            print_warning "Stopping $svc for upgrade..."
            systemctl stop "$svc" || true
            updating=true
        fi
    done
    [[ "$updating" == true ]] && print_info "Upgrading" || print_info "Fresh install"
}

create_user() {
    if [[ "$CREATE_USER" == true ]]; then
        if id "$SEKISHO_USER" &>/dev/null; then
            print_info "User '$SEKISHO_USER' already exists"
        else
            print_info "Creating system user '$SEKISHO_USER'..."
            useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" \
                    -c "Sekisho Sync" "$SEKISHO_USER"
            print_success "User '$SEKISHO_USER' created"
        fi
    fi
}

create_directories() {
    print_info "Creating directories..."
    mkdir -p "$INSTALL_LIB"
    mkdir -p "$DATA_DIR"/{raw,tmp,logs,extract}
    mkdir -p "$CONF_DIR"
    chmod 755 "$DATA_DIR" "$DATA_DIR/raw" "$DATA_DIR/tmp" \
              "$DATA_DIR/logs" "$DATA_DIR/extract"
    if [[ "$CREATE_USER" == true ]]; then
        chown -R "$SEKISHO_USER:$SEKISHO_USER" "$DATA_DIR"
        print_info "Ownership of $DATA_DIR -> $SEKISHO_USER"
    fi
    print_success "Directories ready"
}

setup_pilot_link() {
    local deb_url="https://repo.aosc.io/debs/pool/stable/main/p/pilot-link_0.12.5-5_arm64.deb"
    local deb_tmp="/tmp/pilot-link.deb"
    local extract_dir="/tmp/pilot-extracted"
    local pilot_bin="$PILOT_BIN_DIR/pilot-xfer"

    # ── Step A: ensure vendor dirs exist ─────────────────────────────────────
    mkdir -p "$PILOT_BIN_DIR"
    mkdir -p "$INSTALL_LIB"

    # ── Step B: already present — nothing to do ───────────────────────────────
    if [[ -x "$pilot_bin" ]] && [[ -f "$INSTALL_LIB/libpisock.so.9" ]]; then
        print_success "pilot-xfer already present: $pilot_bin"
        return
    fi

    print_info "Installing pilot-link runtime..."

    # ── Step C: download package ──────────────────────────────────────────────
    print_info "Downloading pilot-link from AOSC..."
    if ! wget -q --show-progress -O "$deb_tmp" "$deb_url"; then
        print_error "Download failed."
        echo "  URL: $deb_url"
        exit 1
    fi
    print_success "Downloaded: $deb_tmp"

    # ── Step D: extract package contents (no system install) ──────────────────
    print_info "Extracting package..."
    rm -rf "$extract_dir"
    if ! dpkg-deb -x "$deb_tmp" "$extract_dir"; then
        print_error "Failed to extract pilot-link package."
        exit 1
    fi

    # ── Step E: copy pilot-xfer binary ────────────────────────────────────────
    if [[ ! -f "$extract_dir/usr/bin/pilot-xfer" ]]; then
        print_error "pilot-xfer not found inside extracted package."
        echo "  Expected: $extract_dir/usr/bin/pilot-xfer"
        exit 1
    fi
    cp "$extract_dir/usr/bin/pilot-xfer" "$pilot_bin"
    chmod +x "$pilot_bin"
    print_success "pilot-xfer binary: $pilot_bin"

    # ── Step F: install libraries ─────────────────────────────────────────────
    print_info "Searching for pilot-link libraries..."

    # Search the entire extract tree — no assumed subdir, no hardcoded arch path.
    # cp -P preserves symlinks (e.g. libpisock.so → libpisock.so.9 → libpisock.so.9.0.0).
    local lib_count=0
    for pattern in "libpisock.so*" "libpisync.so*"; do
        while IFS= read -r -d '' lib_file; do
            print_info "  Found: $lib_file"
            cp -P "$lib_file" "$INSTALL_LIB/"
            (( lib_count++ )) || true
        done < <(find "$extract_dir" -name "$pattern" -print0 2>/dev/null)
    done

    if [[ "$lib_count" -eq 0 ]]; then
        print_error "No pilot-link libraries found in extracted package."
        echo "  Searched: $extract_dir"
        exit 1
    fi
    print_success "Libraries installed: $lib_count files → $INSTALL_LIB"

    # ── Step G: validate libpisock.so.9 present ───────────────────────────────
    if [[ ! -f "$INSTALL_LIB/libpisock.so.9" ]]; then
        print_error "libpisock.so.9 missing after install."
        echo "  Expected: $INSTALL_LIB/libpisock.so.9"
        exit 1
    fi

    # ── Step H: write wrapper with LD_LIBRARY_PATH ────────────────────────────
    cat > "$INSTALL_BIN/pilot-xfer" << EOF
#!/bin/bash
export LD_LIBRARY_PATH="$INSTALL_LIB\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec "$pilot_bin" "\$@"
EOF
    chmod +x "$INSTALL_BIN/pilot-xfer"
    print_success "pilot-xfer wrapper: $INSTALL_BIN/pilot-xfer"

    # ── Step I: final validation ──────────────────────────────────────────────
    if ! LD_LIBRARY_PATH="$INSTALL_LIB" "$pilot_bin" --help &>/dev/null; then
        print_error "pilot-xfer runtime check failed — binary cannot load libraries."
        echo "  Binary:  $pilot_bin"
        echo "  Lib dir: $INSTALL_LIB"
        exit 1
    fi
    print_success "pilot-xfer ready"

    # cleanup
    rm -f "$deb_tmp"
    rm -rf "$extract_dir"
}

setup_venv() {
    print_info "Setting up Python virtual environment..."

    # ── Step 1: Validate existing venv or destroy it ─────────────────────────
    # Do not trust presence alone. A venv can exist but be broken:
    # interrupted install, corrupt pip, missing packages, drift from requirements.
    local venv_ok=false

    if [[ -d "$VENV_DIR" ]]; then
        print_info "venv found at $VENV_DIR — validating..."

        # Check 1: python binary executes
        if ! "$VENV_PYTHON" -c "import sys; sys.exit(0)" 2>/dev/null; then
            print_warning "venv python is not executable. Rebuilding."
        # Check 2: pip is functional
        elif ! "$VENV_PIP" --version &>/dev/null; then
            print_warning "venv pip is not functional. Rebuilding."
        else
            venv_ok=true
            print_info "venv is healthy"
        fi

        if [[ "$venv_ok" == false ]]; then
            print_info "Removing broken venv..."
            rm -rf "$VENV_DIR"
        fi
    fi

    # ── Step 2: Create venv if it doesn't exist (or was just removed) ────────
    if [[ ! -d "$VENV_DIR" ]]; then
        print_info "Creating venv at $VENV_DIR..."
        if ! python3 -m venv "$VENV_DIR"; then
            print_error "Failed to create venv. Is python3-venv installed?"
            exit 1
        fi
        print_success "venv created"
    fi

    # Pin pip to exact version — full determinism, no surprise upgrades
    print_info "Pinning pip to requirements.txt version..."
    if ! "$VENV_PYTHON" -m pip install --quiet pip==24.3.1; then
        print_error "Failed to pin pip version in venv."
        exit 1
    fi

    # ── Step 3: Always reconcile with requirements.txt ───────────────────────
    # pip install with pinned versions is idempotent:
    #   - already correct → no-op
    #   - missing package  → installs it
    #   - wrong version    → corrects it
    print_info "Reconciling dependencies with requirements.txt..."

    if ! "$VENV_PIP" install --quiet -r "$REQUIREMENTS"; then
        print_error "Failed to install dependencies."
        echo ""
        echo "  To debug: $VENV_PIP install -r $REQUIREMENTS"
        exit 1
    fi

    # ── Step 4: Validate critical import ─────────────────────────────────────
    if ! "$VENV_PYTHON" -c "import flask" 2>/dev/null; then
        print_error "Flask not importable from venv after install. Aborting."
        echo ""
        echo "  To debug: $VENV_PYTHON -c 'import flask'"
        exit 1
    fi

    print_success "venv ready"
    print_info "Flask: $("$VENV_PYTHON" -c "import flask; print(flask.__version__)")"
}

install_sources() {
    print_info "Installing source files..."
    cp "$SRC_DIR/config.py"            "$INSTALL_LIB/config.py"
    cp "$SRC_DIR/sekisho_sync.py"      "$INSTALL_LIB/sekisho_sync.py"
    cp "$SRC_DIR/palm_memo_extract.py" "$INSTALL_LIB/palm_memo_extract.py"
    cp "$SRC_DIR/palm_memo_viewer.py"  "$INSTALL_LIB/palm_memo_viewer.py"

    chmod +x "$INSTALL_LIB/sekisho_sync.py"
    chmod +x "$INSTALL_LIB/palm_memo_extract.py"
    chmod +x "$INSTALL_LIB/palm_memo_viewer.py"

    # palm_memo_extract as CLI — uses venv python
    cat > "$INSTALL_BIN/palm_memo_extract" << EOF
#!/bin/bash
exec "$VENV_PYTHON" "$INSTALL_LIB/palm_memo_extract.py" "\$@"
EOF
    chmod +x "$INSTALL_BIN/palm_memo_extract"

    print_success "Sources installed in $INSTALL_LIB"
    print_success "CLI: palm_memo_extract (via venv)"
}

write_config() {
    print_info "Writing config..."
    if [[ ! -f "$CONF_DIR/sekisho.conf" ]]; then
        cat > "$CONF_DIR/sekisho.conf" << EOF
# Sekisho Sync Configuration
# Edit then restart: sudo systemctl restart sekisho-sync sekisho-viewer

# ── Sync daemon ──────────────────────────────────────────────────────────────
SEKISHO_BASE=$DATA_DIR
SEKISHO_TIMEOUT=45
SEKISHO_SLEEP_POLL=5

# ── Viewer ───────────────────────────────────────────────────────────────────
# palm_memo_viewer.py reads: MEMOS_DIR, HOST, PORT, EXTRACT_SCRIPT, etc.
MEMOS_DIR=$DATA_DIR/extract
HOST=0.0.0.0
PORT=$DEFAULT_PORT
EXTRACT_SCRIPT=$INSTALL_BIN/palm_memo_extract
EXTRACT_ARGS=--sekisho --prefix-date
EXTRACT_TIMEOUT=120
EOF
        print_success "Config written: $CONF_DIR/sekisho.conf"
    else
        print_info "Config exists, preserving: $CONF_DIR/sekisho.conf"
    fi
}

migrate_config() {
    # Inject any keys missing from a pre-v1.2.2 config file.
    # write_config preserves existing files — upgrades from older installs
    # never receive new keys. This function patches them in idempotently.
    [[ ! -f "$CONF_DIR/sekisho.conf" ]] && return

    print_info "Checking config for missing keys (migration)..."
    local patched=false

    # Ensure the file ends with a newline before any append.
    # If the last byte is not \n, appending directly would corrupt the
    # final line by joining it with the new key — silent config breakage.
    local last_byte
    last_byte=$(tail -c 1 "$CONF_DIR/sekisho.conf" | wc -c)
    if [[ "$last_byte" -gt 0 ]]; then
        local last_char
        last_char=$(tail -c 1 "$CONF_DIR/sekisho.conf" | od -An -tx1 | tr -d ' ')
        if [[ "$last_char" != "0a" ]]; then
            echo "" >> "$CONF_DIR/sekisho.conf"
            print_info "Added missing trailing newline to config"
        fi
    fi

    # EXTRACT_SCRIPT — absent in installs prior to v1.0.2; causes Sync button
    # to never appear in the viewer regardless of whether the CLI exists.
    if ! grep -q "^EXTRACT_SCRIPT=" "$CONF_DIR/sekisho.conf"; then
        { echo ""; echo "# Added by installer v1.2.2 — required for viewer Sync button";
          echo "EXTRACT_SCRIPT=$INSTALL_BIN/palm_memo_extract"; } >> "$CONF_DIR/sekisho.conf"
        print_success "Migrated: added EXTRACT_SCRIPT to existing config"
        patched=true
    fi

    # EXTRACT_ARGS — absent in installs prior to v1.0.2
    if ! grep -q "^EXTRACT_ARGS=" "$CONF_DIR/sekisho.conf"; then
        echo "EXTRACT_ARGS=--sekisho --prefix-date" >> "$CONF_DIR/sekisho.conf"
        print_success "Migrated: added EXTRACT_ARGS to existing config"
        patched=true
    fi

    # EXTRACT_TIMEOUT — absent in installs prior to v1.0.2
    if ! grep -q "^EXTRACT_TIMEOUT=" "$CONF_DIR/sekisho.conf"; then
        echo "EXTRACT_TIMEOUT=120" >> "$CONF_DIR/sekisho.conf"
        print_success "Migrated: added EXTRACT_TIMEOUT to existing config"
        patched=true
    fi

    [[ "$patched" == false ]] && print_info "Config up to date, no migration needed"
}

create_services() {
    print_info "Creating systemd services..."

    # sekisho-sync
    {
        echo "[Unit]"
        echo "Description=Sekisho Sync — Palm Pilot NetSync Backup Daemon"
        echo "After=network.target"
        echo ""
        echo "[Service]"
        echo "Type=simple"
        echo "EnvironmentFile=$CONF_DIR/sekisho.conf"
        echo "ExecStart=$VENV_PYTHON $INSTALL_LIB/sekisho_sync.py"
        echo "Restart=always"
        echo "RestartSec=10"
        [[ "$CREATE_USER" == true ]] && echo "User=$SEKISHO_USER" && echo "Group=$SEKISHO_USER"
        echo "WorkingDirectory=$DATA_DIR"
        echo ""
        echo "NoNewPrivileges=true"
        echo "PrivateTmp=true"
        echo "ProtectSystem=strict"
        echo "ProtectHome=true"
        echo "ReadWritePaths=$DATA_DIR"
        echo ""
        echo "StandardOutput=journal"
        echo "StandardError=journal"
        echo "SyslogIdentifier=$SYNC_SERVICE"
        echo ""
        echo "[Install]"
        echo "WantedBy=multi-user.target"
    } > "$SYSTEMD_DIR/$SYNC_SERVICE.service"

    # sekisho-viewer
    {
        echo "[Unit]"
        echo "Description=Sekisho Viewer — Palm Memo Web Interface"
        echo "After=network.target"
        echo ""
        echo "[Service]"
        echo "Type=simple"
        echo "EnvironmentFile=$CONF_DIR/sekisho.conf"
        echo "ExecStart=$VENV_PYTHON $INSTALL_LIB/palm_memo_viewer.py"
        echo "Restart=always"
        echo "RestartSec=10"
        [[ "$CREATE_USER" == true ]] && echo "User=$SEKISHO_USER" && echo "Group=$SEKISHO_USER"
        echo "WorkingDirectory=$DATA_DIR"
        echo ""
        echo "NoNewPrivileges=true"
        echo "PrivateTmp=true"
        echo "ProtectSystem=strict"
        echo "ProtectHome=true"
        echo "ReadWritePaths=$DATA_DIR"
        echo ""
        echo "StandardOutput=journal"
        echo "StandardError=journal"
        echo "SyslogIdentifier=$VIEWER_SERVICE"
        echo ""
        echo "[Install]"
        echo "WantedBy=multi-user.target"
    } > "$SYSTEMD_DIR/$VIEWER_SERVICE.service"

    print_success "Services created (using venv python: $VENV_PYTHON)"
}

setup_logrotate() {
    print_info "Configuring logrotate..."
    local log_owner="root"
    [[ "$CREATE_USER" == true ]] && log_owner="$SEKISHO_USER"
    cat > "$LOGROTATE_DIR/sekisho-sync" << EOF
$DATA_DIR/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0644 $log_owner $log_owner
}
EOF
    print_success "Logrotate configured (owner: $log_owner)"
}

enable_and_start() {
    print_info "Enabling and starting services..."
    systemctl daemon-reload
    systemctl enable "$SYNC_SERVICE" "$VIEWER_SERVICE"

    set +e
    systemctl start "$SYNC_SERVICE"
    sleep 2
    systemctl is-active --quiet "$SYNC_SERVICE"
    local sync_ok=$?

    systemctl start "$VIEWER_SERVICE"
    sleep 3
    systemctl is-active --quiet "$VIEWER_SERVICE"
    local viewer_ok=$?
    set -e

    [[ $sync_ok -eq 0 ]]   && print_success "$SYNC_SERVICE running"   || print_error "$SYNC_SERVICE failed — check: journalctl -u $SYNC_SERVICE -n 30"
    [[ $viewer_ok -eq 0 ]] && print_success "$VIEWER_SERVICE running" || print_error "$VIEWER_SERVICE failed — check: journalctl -u $VIEWER_SERVICE -n 30"

    [[ $sync_ok -ne 0 || $viewer_ok -ne 0 ]] && exit 1
}

show_summary() {
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}') || ip="<your-pi-ip>"

    echo ""
    echo "=========================================="
    echo -e "${GREEN}  Sekisho Sync v1.2.2 — Installed${NC}"
    echo "=========================================="
    echo ""
    echo "  Sync daemon:  24/7, NetSync port $NETSYNC_PORT"
    echo "  Viewer:       http://$ip:$DEFAULT_PORT"
    echo "  venv:         $VENV_DIR"
    echo "  Python:       $VENV_PYTHON"
    if [[ "$CREATE_USER" == true ]]; then
        echo -e "  User:         ${GREEN}$SEKISHO_USER (unprivileged)${NC}"
    else
        echo -e "  User:         ${RED}root — consider reinstalling without --no-create-user${NC}"
    fi
    echo ""
    echo "  Data:         $DATA_DIR"
    echo "  Config:       $CONF_DIR/sekisho.conf"
    echo "  Logs:         journalctl -u sekisho-sync -f"
    echo "                journalctl -u sekisho-viewer -f"
    echo ""
    echo "  On-demand extract:"
    echo "    palm_memo_extract --sekisho --out $DATA_DIR/extract --prefix-date"
    echo "=========================================="
}

# =============================================================================
main() {
    parse_args "$@"

    echo ""
    echo "=========================================="
    echo "  Sekisho Sync — Installer v1.2.2"
    echo "=========================================="
    echo ""

    check_root
    check_arch
    confirm_root_execution
    check_dependencies
    check_ports
    check_existing
    create_user
    create_directories
    setup_pilot_link
    setup_venv
    install_sources
    write_config
    migrate_config
    create_services
    setup_logrotate
    enable_and_start
    show_summary
}

main "$@"
