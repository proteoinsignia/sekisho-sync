# Sekisho Sync

> **Palm OS sync, finally reliable on modern Linux.**

Sekisho Sync is a self-contained Palm OS NetSync service that provides stable wireless synchronization, extraction, and viewing of Palm device data — without relying on fragile system packages.

---

## 🚀 Overview

Sekisho Sync bridges legacy Palm OS devices with modern systems through a deterministic, WiFi-based synchronization runtime.

It runs as a background service, automatically handling data extraction and exposing a lightweight web interface for access.

Unlike traditional setups, Sekisho **does not depend on system-installed `pilot-link` packages**.
Instead, it ships its own controlled runtime to eliminate dependency conflicts and ensure consistent behavior across systems.

---

## ✨ Features

* 📡 **WiFi NetSync support** (primary transport)
* 🔒 **Self-contained runtime** (bundled `pilot-xfer` + core libraries)
* ⚙️ **Systemd integration** (runs automatically in background)
* 🧠 **Deterministic installer** (no broken dependencies, no distro issues)
* 📂 **Automatic memo extraction**
* 🌐 **Web-based viewer (Flask)**
* ♻️ **Safe upgrades and idempotent installs**

---

## 🧩 Runtime Design

Sekisho Sync includes a vendored `pilot-xfer` runtime extracted from a known working build.

This runtime is derived from a stable AOSC package and adapted to ensure compatibility with modern Linux systems.

This design ensures:

* Consistent behavior across systems
* Elimination of dependency conflicts between distributions
* Reliable installation without external repositories

Traditional `pilot-link` packages vary significantly across Linux distributions and may introduce incompatibilities due to:

* differing dependency names
* library version mismatches
* packaging inconsistencies

By bundling only the required runtime components (`pilot-xfer` and its core libraries), Sekisho avoids these issues while remaining compatible with standard system libraries.

---

## 🧱 Architecture

```id="arch-premium"
Sekisho Sync Runtime
 ├── Python (isolated virtual environment)
 ├── pilot-xfer (vendored binary)
 ├── libpisock / libpisync (bundled)
 └── systemd services
```

---

## 📦 Requirements

* Linux (ARM64 / aarch64)
* Python 3.11+
* systemd

### ✅ Tested Environment

Sekisho Sync is actively tested and verified on:

* Debian 13 (Trixie) — ARM64

Compatibility with other distributions is expected but not guaranteed.

### Implicit system libraries

Most modern systems already include:

* glibc
* libusb (legacy compatibility)

No manual dependency installation is typically required.

---

## ⚡ Installation

```bash id="install-premium"
git clone https://github.com/proteoinsignia/sekisho-sync.git
cd sekisho-sync
sudo ./install.sh
```

The installer will:

* Create a dedicated system user
* Install runtime in `/opt/sekisho-sync`
* Bundle required binaries and libraries
* Configure systemd services
* Start services automatically

---

## 🔁 Upgrade

```bash id="upgrade-premium"
sudo ./install.sh
```

Sekisho will:

* Stop services safely
* Update runtime and code
* Preserve configuration
* Restart services

---

## 🗑️ Uninstall

```bash id="uninstall-premium"
sudo ./uninstall.sh
```

Removes all installed components, including services and runtime.

---

## 🌐 Web Viewer

Once running:

```id="viewer-premium"
http://<your-ip>:5000
```

Provides access to extracted Palm memos and data.

---

## 🧪 Validation

```bash id="validation-premium"
pilot-xfer --help
systemctl status sekisho-sync
systemctl status sekisho-viewer
```

---

## ⚠️ Transport Support

Currently supported:

* ✅ WiFi NetSync (stable)

---

## 🔮 Future Support

Sekisho Sync is designed with a modular transport architecture.

Future versions may introduce:

* Bluetooth (RFCOMM-based sync)
* USB / Serial bridges
* Additional legacy interfaces

These features are not yet implemented and should be considered experimental.

---

## 🧭 Philosophy

> Control the runtime. Eliminate external fragility.

Sekisho Sync avoids reliance on inconsistent distribution packages by bundling only what is required — ensuring predictable and repeatable behavior across systems.

---

## 📜 License

MIT License

---

## 👤 Author

**Proteoinsignia**

---

## 🔥 Status

**Beta – Production-capable for technical users**

---

## 💬 Final Note

Sekisho Sync is not just a wrapper around legacy tools.

It is a controlled runtime designed to make Palm synchronization reliable again on modern systems.
