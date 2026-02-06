import os
import json
import subprocess
import platform
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QGroupBox, QCheckBox, QGridLayout,
    QSpacerItem, QSizePolicy, QProgressBar, QFrame, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

# Try to import psutil for system resources
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Try to import httpx for API calls
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# Paths
CONFIG_DIR = Path.home() / ".apn"
CONFIG_PATH = CONFIG_DIR / "node_config.json"
CONTRIBUTION_PATH = CONFIG_DIR / "contribution_settings.json"
PROFILE_PATH = Path.home() / ".alpha_protocol_network" / "profile.json"

# APN Core constants
APN_CORE_VERSION = "1.0.0"
DEFAULT_NATS_RELAY = "nats://nonlocal.info:4222"

class APNPage(QWidget):
    def __init__(self, config=None):
        super().__init__()
        self.apn_config = config
        self.setWindowTitle("Alpha Protocol Network Control Panel")

        self.payment_address = self.load_payment_address()

        main_layout = QVBoxLayout(self)

        # ================
        # NODE IDENTITY
        # ================
        identity_group = QGroupBox("Node Identity")
        identity_layout = QGridLayout()

        identity_layout.addWidget(QLabel("Node ID:"), 0, 0)
        self.node_id_input = QLineEdit()
        self.node_id_input.setPlaceholderText("e.g. AlphaGenesis01")
        identity_layout.addWidget(self.node_id_input, 0, 1)

        identity_layout.addWidget(QLabel("Payment Address:"), 1, 0)
        self.payment_address_input = QLineEdit()
        self.payment_address_input.setText(self.payment_address)
        self.payment_address_input.setReadOnly(True)
        identity_layout.addWidget(self.payment_address_input, 1, 1)

        identity_group.setLayout(identity_layout)
        main_layout.addWidget(identity_group)

        # =====================
        # ACCESS POINT SETTINGS
        # =====================
        ap_group = QGroupBox("WiFi Access Point Settings")
        ap_layout = QGridLayout()

        ap_layout.addWidget(QLabel("SSID:"), 0, 0)
        self.ssid_input = QLineEdit()
        self.ssid_input.setPlaceholderText("AlphaProtocolNetwork")
        ap_layout.addWidget(self.ssid_input, 0, 1)

        ap_layout.addWidget(QLabel("Password:"), 1, 0)
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Minimum 8 characters")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        ap_layout.addWidget(self.password_input, 1, 1)

        ap_layout.addWidget(QLabel("Channel:"), 2, 0)
        self.channel_input = QLineEdit()
        self.channel_input.setPlaceholderText("6")
        ap_layout.addWidget(self.channel_input, 2, 1)

        self.bridging_checkbox = QCheckBox("Allow connected nodes to access WWW")
        ap_layout.addWidget(self.bridging_checkbox, 3, 0, 1, 2)

        self.vpn_checkbox = QCheckBox("Enable VPN Encryption for APN traffic")
        ap_layout.addWidget(self.vpn_checkbox, 4, 0, 1, 2)

        button_row = QHBoxLayout()
        self.start_button = QPushButton("Start Access Point")
        self.start_button.clicked.connect(self.start_access_point)
        self.stop_button = QPushButton("Stop Access Point")
        self.stop_button.clicked.connect(self.stop_access_point)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        ap_layout.addLayout(button_row, 5, 0, 1, 2)

        ap_group.setLayout(ap_layout)
        main_layout.addWidget(ap_group)

        # ==================
        # ROLES AND SERVICES
        # ==================
        # Relay Node
        relay_group = QGroupBox("Relay Node")
        relay_layout = QVBoxLayout()
        self.relay_checkbox = QCheckBox("Enable this node as a Relay")
        relay_layout.addWidget(self.relay_checkbox)
        relay_group.setLayout(relay_layout)
        main_layout.addWidget(relay_group)

        # Storage Node
        storage_group = QGroupBox("Storage Node")
        storage_layout = QGridLayout()
        self.storage_checkbox = QCheckBox("Enable Storage Service")
        storage_layout.addWidget(self.storage_checkbox, 0, 0, 1, 2)
        self.storage_gb_label = QLabel("Available Storage (GB):")
        self.storage_gb_input = QLineEdit()
        self.storage_price_label = QLabel("Price per GB (Alpha):")
        self.storage_price_input = QLineEdit()
        storage_layout.addWidget(self.storage_gb_label, 1, 0)
        storage_layout.addWidget(self.storage_gb_input, 1, 1)
        storage_layout.addWidget(self.storage_price_label, 2, 0)
        storage_layout.addWidget(self.storage_price_input, 2, 1)
        storage_group.setLayout(storage_layout)
        main_layout.addWidget(storage_group)

        # Compute Node
        compute_group = QGroupBox("Compute Node")
        compute_layout = QGridLayout()
        self.compute_checkbox = QCheckBox("Enable Compute Service")
        compute_layout.addWidget(self.compute_checkbox, 0, 0, 1, 2)
        self.compute_cores_label = QLabel("CPU Cores:")
        self.compute_cores_input = QLineEdit()
        self.compute_price_label = QLabel("Price per Second (Alpha):")
        self.compute_price_input = QLineEdit()
        compute_layout.addWidget(self.compute_cores_label, 1, 0)
        compute_layout.addWidget(self.compute_cores_input, 1, 1)
        compute_layout.addWidget(self.compute_price_label, 2, 0)
        compute_layout.addWidget(self.compute_price_input, 2, 1)
        compute_group.setLayout(compute_layout)
        main_layout.addWidget(compute_group)

        # Bridge / Gateway
        bridge_group = QGroupBox("Internet Bridge / Gateway")
        bridge_layout = QGridLayout()
        self.bridge_checkbox = QCheckBox("Enable Internet Bridging")
        bridge_layout.addWidget(self.bridge_checkbox, 0, 0, 1, 2)
        self.bridge_region_label = QLabel("Region:")
        self.bridge_region_input = QLineEdit()
        self.bridge_price_label = QLabel("Price per MB (Alpha):")
        self.bridge_price_input = QLineEdit()
        bridge_layout.addWidget(self.bridge_region_label, 1, 0)
        bridge_layout.addWidget(self.bridge_region_input, 1, 1)
        bridge_layout.addWidget(self.bridge_price_label, 2, 0)
        bridge_layout.addWidget(self.bridge_price_input, 2, 1)
        bridge_group.setLayout(bridge_layout)
        main_layout.addWidget(bridge_group)

        # ==================
        # DEVICE CONTRIBUTION (NEW - APN Core)
        # ==================
        contribution_group = QGroupBox(f"Device Contribution - APN Core v{APN_CORE_VERSION}")
        contribution_layout = QVBoxLayout()

        # System Resources Display
        resources_frame = QFrame()
        resources_frame.setFrameShape(QFrame.Shape.StyledPanel)
        resources_layout = QGridLayout(resources_frame)

        resources_layout.addWidget(QLabel("System Resources:"), 0, 0, 1, 4)

        # CPU
        resources_layout.addWidget(QLabel("CPU:"), 1, 0)
        self.cpu_label = QLabel("Detecting...")
        resources_layout.addWidget(self.cpu_label, 1, 1)
        self.cpu_progress = QProgressBar()
        self.cpu_progress.setMaximum(100)
        resources_layout.addWidget(self.cpu_progress, 1, 2, 1, 2)

        # Memory
        resources_layout.addWidget(QLabel("RAM:"), 2, 0)
        self.ram_label = QLabel("Detecting...")
        resources_layout.addWidget(self.ram_label, 2, 1)
        self.ram_progress = QProgressBar()
        self.ram_progress.setMaximum(100)
        resources_layout.addWidget(self.ram_progress, 2, 2, 1, 2)

        # Storage
        resources_layout.addWidget(QLabel("Storage:"), 3, 0)
        self.storage_label = QLabel("Detecting...")
        resources_layout.addWidget(self.storage_label, 3, 1)
        self.storage_progress = QProgressBar()
        self.storage_progress.setMaximum(100)
        resources_layout.addWidget(self.storage_progress, 3, 2, 1, 2)

        # GPU
        resources_layout.addWidget(QLabel("GPU:"), 4, 0)
        self.gpu_label = QLabel("Detecting...")
        resources_layout.addWidget(self.gpu_label, 4, 1, 1, 3)

        contribution_layout.addWidget(resources_frame)

        # Contribution Settings
        contrib_settings_layout = QGridLayout()

        self.contrib_enabled_checkbox = QCheckBox("Enable Device Contribution")
        self.contrib_enabled_checkbox.setToolTip("Contribute your device resources to the APN mesh network")
        contrib_settings_layout.addWidget(self.contrib_enabled_checkbox, 0, 0, 1, 2)

        self.contrib_relay_checkbox = QCheckBox("Relay (Network Traffic)")
        self.contrib_relay_checkbox.setToolTip("Allow your node to relay mesh traffic")
        contrib_settings_layout.addWidget(self.contrib_relay_checkbox, 1, 0)

        self.contrib_compute_checkbox = QCheckBox("Compute (CPU Processing)")
        self.contrib_compute_checkbox.setToolTip("Contribute CPU cycles for distributed computing")
        contrib_settings_layout.addWidget(self.contrib_compute_checkbox, 1, 1)

        self.contrib_storage_checkbox = QCheckBox("Storage (Distributed Storage)")
        self.contrib_storage_checkbox.setToolTip("Contribute storage space to the network")
        contrib_settings_layout.addWidget(self.contrib_storage_checkbox, 2, 0)

        contrib_settings_layout.addWidget(QLabel("Storage Allocation (GB):"), 3, 0)
        self.contrib_storage_input = QLineEdit()
        self.contrib_storage_input.setPlaceholderText("10")
        self.contrib_storage_input.setText("10")
        contrib_settings_layout.addWidget(self.contrib_storage_input, 3, 1)

        contrib_settings_layout.addWidget(QLabel("Compute Cores:"), 4, 0)
        self.contrib_cores_input = QLineEdit()
        self.contrib_cores_input.setPlaceholderText("1")
        self.contrib_cores_input.setText("1")
        contrib_settings_layout.addWidget(self.contrib_cores_input, 4, 1)

        contribution_layout.addLayout(contrib_settings_layout)

        # Network Connection Status
        network_frame = QFrame()
        network_frame.setFrameShape(QFrame.Shape.StyledPanel)
        network_layout = QHBoxLayout(network_frame)

        network_layout.addWidget(QLabel("NATS Relay:"))
        self.relay_status_label = QLabel(DEFAULT_NATS_RELAY)
        network_layout.addWidget(self.relay_status_label)

        self.mesh_peers_label = QLabel("Peers: 0")
        network_layout.addWidget(self.mesh_peers_label)

        self.contrib_status_label = QLabel("Status: Idle")
        self.contrib_status_label.setStyleSheet("font-weight: bold;")
        network_layout.addWidget(self.contrib_status_label)

        contribution_layout.addWidget(network_frame)

        # Contribution Buttons
        contrib_buttons_layout = QHBoxLayout()

        self.start_contrib_button = QPushButton("Start Contributing")
        self.start_contrib_button.clicked.connect(self.start_contribution)
        self.start_contrib_button.setStyleSheet("background-color: #2d5a27; color: white;")
        contrib_buttons_layout.addWidget(self.start_contrib_button)

        self.stop_contrib_button = QPushButton("Stop Contributing")
        self.stop_contrib_button.clicked.connect(self.stop_contribution)
        self.stop_contrib_button.setStyleSheet("background-color: #5a2727; color: white;")
        contrib_buttons_layout.addWidget(self.stop_contrib_button)

        self.refresh_resources_button = QPushButton("Refresh Resources")
        self.refresh_resources_button.clicked.connect(self.refresh_system_resources)
        contrib_buttons_layout.addWidget(self.refresh_resources_button)

        contribution_layout.addLayout(contrib_buttons_layout)

        contribution_group.setLayout(contribution_layout)
        main_layout.addWidget(contribution_group)

        # ==================
        # SAVE / LOAD BUTTONS
        # ==================
        buttons_layout = QHBoxLayout()
        self.save_button = QPushButton("Save Config")
        self.save_button.clicked.connect(self.save_config)
        self.load_button = QPushButton("Load Config")
        self.load_button.clicked.connect(self.load_config)
        buttons_layout.addWidget(self.save_button)
        buttons_layout.addWidget(self.load_button)
        main_layout.addLayout(buttons_layout)

        main_layout.addItem(QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        # Auto-load configs
        self.load_config()
        self.load_contribution_settings()

        # Initial resource refresh
        self.refresh_system_resources()

        # Setup periodic resource refresh (every 10 seconds)
        self.resource_timer = QTimer()
        self.resource_timer.timeout.connect(self.refresh_system_resources)
        self.resource_timer.start(10000)

    # ================
    # PAYMENT ADDRESS
    # ================
    def load_payment_address(self):
        try:
            with open(PROFILE_PATH) as f:
                data = json.load(f)
                return data.get("address", "")
        except Exception:
            return ""

    # ================
    # SAVE CONFIG
    # ================
    def save_config(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            config = {
                "nodeId": self.node_id_input.text().strip(),
                "paymentAddress": self.payment_address_input.text().strip(),
                "roles": [],
                "settings": {}
            }

            if self.relay_checkbox.isChecked():
                config["roles"].append("Relay")

            if self.storage_checkbox.isChecked():
                config["roles"].append("Storage")
                config["settings"]["storage"] = {
                    "availableGB": int(self.storage_gb_input.text()),
                    "pricePerGB": int(self.storage_price_input.text())
                }

            if self.compute_checkbox.isChecked():
                config["roles"].append("Compute")
                config["settings"]["compute"] = {
                    "cpuCores": int(self.compute_cores_input.text()),
                    "pricePerSecond": int(self.compute_price_input.text())
                }

            if self.bridge_checkbox.isChecked():
                config["roles"].append("Bridge")
                config["settings"]["bridge"] = {
                    "region": self.bridge_region_input.text().strip(),
                    "pricePerMB": int(self.bridge_price_input.text())
                }

            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=2)

            QMessageBox.information(self, "Success", "Node config saved!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save config: {e}")

    # ================
    # LOAD CONFIG
    # ================
    def load_config(self):
        if not CONFIG_PATH.exists():
            return

        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)

            self.node_id_input.setText(config.get("nodeId", ""))
            self.payment_address_input.setText(config.get("paymentAddress", ""))
            roles = config.get("roles", [])

            self.relay_checkbox.setChecked("Relay" in roles)
            self.storage_checkbox.setChecked("Storage" in roles)
            self.compute_checkbox.setChecked("Compute" in roles)
            self.bridge_checkbox.setChecked("Bridge" in roles)

            settings = config.get("settings", {})
            if "storage" in settings:
                self.storage_gb_input.setText(str(settings["storage"].get("availableGB", "")))
                self.storage_price_input.setText(str(settings["storage"].get("pricePerGB", "")))
            if "compute" in settings:
                self.compute_cores_input.setText(str(settings["compute"].get("cpuCores", "")))
                self.compute_price_input.setText(str(settings["compute"].get("pricePerSecond", "")))
            if "bridge" in settings:
                self.bridge_region_input.setText(settings["bridge"].get("region", ""))
                self.bridge_price_input.setText(str(settings["bridge"].get("pricePerMB", "")))

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load config: {e}")

    # ================
    # ACCESS POINT MANAGEMENT (LOCAL ONLY)
    # ================
    def start_access_point(self):
        ssid = self.ssid_input.text().strip()
        password = self.password_input.text().strip()
        channel = self.channel_input.text().strip() or "6"

        if len(ssid) < 1:
            self.show_message("Error", "SSID cannot be empty.")
            return
        if len(password) < 8:
            self.show_message("Error", "Password must be at least 8 characters.")
            return

        try:
            hostapd_conf = f"""
interface=wlan0
driver=nl80211
ssid={ssid}
hw_mode=g
channel={channel}
wmm_enabled=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
"""
            os.makedirs("/etc/hostapd", exist_ok=True)
            with open("/etc/hostapd/hostapd.conf", "w") as f:
                f.write(hostapd_conf)

            subprocess.run(["sudo", "systemctl", "restart", "hostapd"], check=True)
            subprocess.run(["sudo", "systemctl", "restart", "dnsmasq"], check=True)

            if self.bridging_checkbox.isChecked():
                subprocess.run(["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"], check=True)
                subprocess.run(["sudo", "iptables", "-t", "nat", "-A", "POSTROUTING", "-o", "eth0", "-j", "MASQUERADE"], check=True)

            self.show_message("Success", f"Access Point '{ssid}' started successfully!")
        except Exception as e:
            self.show_message("Error", f"Failed to start AP: {e}")

    def stop_access_point(self):
        try:
            subprocess.run(["sudo", "systemctl", "stop", "hostapd"], check=True)
            subprocess.run(["sudo", "systemctl", "stop", "dnsmasq"], check=True)
            subprocess.run(["sudo", "iptables", "-t", "nat", "-D", "POSTROUTING", "-o", "eth0", "-j", "MASQUERADE"], check=True)
            self.show_message("Stopped", "Access Point stopped successfully.")
        except Exception as e:
            self.show_message("Error", f"Failed to stop AP: {e}")

    def show_message(self, title, message):
        QMessageBox.information(self, title, message)

    # ================
    # DEVICE CONTRIBUTION METHODS
    # ================
    def refresh_system_resources(self):
        """Refresh system resource information"""
        try:
            # CPU
            cpu_count = os.cpu_count() or 1
            if PSUTIL_AVAILABLE:
                cpu_percent = psutil.cpu_percent(interval=0.1)
                self.cpu_label.setText(f"{cpu_count} cores")
                self.cpu_progress.setValue(int(cpu_percent))
            else:
                self.cpu_label.setText(f"{cpu_count} cores (psutil not installed)")
                self.cpu_progress.setValue(0)

            # Memory
            if PSUTIL_AVAILABLE:
                mem = psutil.virtual_memory()
                total_gb = round(mem.total / (1024**3), 1)
                used_percent = mem.percent
                self.ram_label.setText(f"{total_gb} GB")
                self.ram_progress.setValue(int(used_percent))
            else:
                self.ram_label.setText("Unknown")
                self.ram_progress.setValue(0)

            # Storage
            if PSUTIL_AVAILABLE:
                disk = psutil.disk_usage('/')
                total_gb = round(disk.total / (1024**3), 1)
                free_gb = round(disk.free / (1024**3), 1)
                used_percent = disk.percent
                self.storage_label.setText(f"{free_gb} GB free / {total_gb} GB")
                self.storage_progress.setValue(int(used_percent))
            else:
                self.storage_label.setText("Unknown")
                self.storage_progress.setValue(0)

            # GPU detection
            try:
                result = subprocess.run(
                    ['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    parts = result.stdout.strip().split(',')
                    if len(parts) >= 2:
                        gpu_name = parts[0].strip()
                        gpu_mem = round(int(parts[1].strip()) / 1024, 1)
                        self.gpu_label.setText(f"{gpu_name} ({gpu_mem} GB)")
                    else:
                        self.gpu_label.setText("No GPU detected")
                else:
                    self.gpu_label.setText("No NVIDIA GPU detected")
            except Exception:
                self.gpu_label.setText("No GPU detected")

            # Try to get mesh peer count from Pythia Master API
            if HTTPX_AVAILABLE:
                try:
                    # First try Pythia Master for network-wide view
                    with httpx.Client(timeout=2.0) as client:
                        response = client.get("http://192.168.1.77:8081/api/status")
                        if response.status_code == 200:
                            data = response.json()
                            peer_count = len(data.get('peers', []))
                            self.mesh_peers_label.setText(f"Network Peers: {peer_count}")
                        else:
                            # Fallback to local API
                            response = client.get("http://127.0.0.1:8000/api/mesh/peers")
                            if response.status_code == 200:
                                data = response.json()
                                peer_count = len(data.get('peers', []))
                                self.mesh_peers_label.setText(f"Local Peers: {peer_count}")
                except Exception:
                    self.mesh_peers_label.setText("Peers: ?")

        except Exception as e:
            print(f"Error refreshing resources: {e}")

    def start_contribution(self):
        """Start contributing device resources to the network"""
        try:
            settings = {
                "enabled": True,
                "relay": self.contrib_relay_checkbox.isChecked(),
                "compute": self.contrib_compute_checkbox.isChecked(),
                "storage": self.contrib_storage_checkbox.isChecked(),
                "storage_gb_allocated": int(self.contrib_storage_input.text() or "10"),
                "compute_cores_allocated": int(self.contrib_cores_input.text() or "1"),
                "bandwidth_limit_mbps": 100,
            }

            # Save locally
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONTRIBUTION_PATH, 'w') as f:
                json.dump(settings, f, indent=2)

            # Try to update the APN server
            if HTTPX_AVAILABLE:
                try:
                    with httpx.Client(timeout=5.0) as client:
                        response = client.post(
                            "http://127.0.0.1:8000/api/contribution/settings",
                            json=settings
                        )
                        if response.status_code == 200:
                            self.contrib_status_label.setText("Status: Contributing")
                            self.contrib_status_label.setStyleSheet("font-weight: bold; color: #00ff88;")
                            self.show_message("Success", "Device contribution started!\n\nYour node is now contributing to the Alpha Protocol Network.")
                            return
                except Exception as e:
                    print(f"Failed to update APN server: {e}")

            # Fallback - just show local save success
            self.contrib_status_label.setText("Status: Contributing (local)")
            self.contrib_status_label.setStyleSheet("font-weight: bold; color: #ffaa00;")
            self.show_message("Settings Saved", "Contribution settings saved locally.\nAPN server not running - settings will apply on next startup.")

        except Exception as e:
            self.show_message("Error", f"Failed to start contribution: {e}")

    def stop_contribution(self):
        """Stop contributing device resources"""
        try:
            settings = {
                "enabled": False,
                "relay": False,
                "compute": False,
                "storage": False,
                "storage_gb_allocated": 0,
                "compute_cores_allocated": 0,
                "bandwidth_limit_mbps": 0,
            }

            # Save locally
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONTRIBUTION_PATH, 'w') as f:
                json.dump(settings, f, indent=2)

            # Try to update the APN server
            if HTTPX_AVAILABLE:
                try:
                    with httpx.Client(timeout=5.0) as client:
                        response = client.post(
                            "http://127.0.0.1:8000/api/contribution/settings",
                            json=settings
                        )
                except Exception:
                    pass

            self.contrib_status_label.setText("Status: Idle")
            self.contrib_status_label.setStyleSheet("font-weight: bold; color: #888;")

            # Uncheck all contribution options
            self.contrib_enabled_checkbox.setChecked(False)
            self.contrib_relay_checkbox.setChecked(False)
            self.contrib_compute_checkbox.setChecked(False)
            self.contrib_storage_checkbox.setChecked(False)

            self.show_message("Stopped", "Device contribution stopped.")

        except Exception as e:
            self.show_message("Error", f"Failed to stop contribution: {e}")

    def load_contribution_settings(self):
        """Load contribution settings from file"""
        if not CONTRIBUTION_PATH.exists():
            return

        try:
            with open(CONTRIBUTION_PATH) as f:
                settings = json.load(f)

            self.contrib_enabled_checkbox.setChecked(settings.get("enabled", False))
            self.contrib_relay_checkbox.setChecked(settings.get("relay", False))
            self.contrib_compute_checkbox.setChecked(settings.get("compute", False))
            self.contrib_storage_checkbox.setChecked(settings.get("storage", False))
            self.contrib_storage_input.setText(str(settings.get("storage_gb_allocated", 10)))
            self.contrib_cores_input.setText(str(settings.get("compute_cores_allocated", 1)))

            if settings.get("enabled"):
                self.contrib_status_label.setText("Status: Contributing")
                self.contrib_status_label.setStyleSheet("font-weight: bold; color: #00ff88;")
            else:
                self.contrib_status_label.setText("Status: Idle")
                self.contrib_status_label.setStyleSheet("font-weight: bold; color: #888;")

        except Exception as e:
            print(f"Failed to load contribution settings: {e}")
