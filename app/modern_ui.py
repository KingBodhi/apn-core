"""
APN Core - Modern UI
Clean, minimal interface for network contribution
"""

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QApplication
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QColor, QPalette

import json
import sys
from pathlib import Path

try:
    import psutil
    import httpx
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class ModernCard(QFrame):
    """Modern card component with subtle shadow"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border-radius: 12px;
                border: 1px solid #2d2d2d;
                padding: 20px;
            }
            QFrame:hover {
                border: 1px solid #3d3d3d;
            }
        """)


class StatusIndicator(QWidget):
    """Animated status indicator dot"""

    def __init__(self, status="offline", parent=None):
        super().__init__(parent)
        self.status = status
        self.setFixedSize(12, 12)
        self.update_status(status)

    def update_status(self, status):
        self.status = status
        colors = {
            "online": "#10b981",  # Green
            "offline": "#6b7280",  # Gray
            "warning": "#f59e0b",  # Orange
            "error": "#ef4444"  # Red
        }
        color = colors.get(status, "#6b7280")
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {color};
                border-radius: 6px;
            }}
        """)


class APNModernUI(QMainWindow):
    """Modern APN Core UI"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("APN Core")
        self.setMinimumSize(800, 600)

        # Load node info
        self.node_id = "Loading..."
        self.wallet_address = "Loading..."
        self.contribution_enabled = False

        self.setup_ui()
        self.load_node_info()
        self.load_contribution_settings()

        # Auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_data)
        self.refresh_timer.start(5000)  # Refresh every 5 seconds

        self.refresh_data()

    def setup_ui(self):
        """Setup the user interface"""

        # Main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(20)

        # Apply dark theme
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0a0a0a;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 24px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #374151;
                color: #6b7280;
            }
        """)

        # Header
        header = self.create_header()
        main_layout.addWidget(header)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(20)

        # Cards
        scroll_layout.addWidget(self.create_wallet_card())
        scroll_layout.addWidget(self.create_status_card())
        scroll_layout.addWidget(self.create_contribution_card())
        scroll_layout.addWidget(self.create_resources_card())
        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll)

    def create_header(self):
        """Create header with logo and title"""
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 20)

        # Logo/Title
        title_layout = QVBoxLayout()
        title = QLabel("APN Core")
        title.setFont(QFont("SF Pro Display", 32, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff;")

        subtitle = QLabel("Alpha Protocol Network")
        subtitle.setFont(QFont("SF Pro Display", 14))
        subtitle.setStyleSheet("color: #6b7280;")

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)

        layout.addLayout(title_layout)
        layout.addStretch()

        # Version
        version = QLabel("v2.0")
        version.setFont(QFont("SF Mono", 12))
        version.setStyleSheet("""
            color: #6b7280;
            background-color: #1e1e1e;
            border-radius: 6px;
            padding: 6px 12px;
        """)
        layout.addWidget(version, alignment=Qt.AlignmentFlag.AlignTop)

        return header

    def create_wallet_card(self):
        """Wallet address card"""
        card = ModernCard()
        layout = QVBoxLayout(card)
        layout.setSpacing(12)

        # Header
        header = QLabel("Wallet Address")
        header.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        header.setStyleSheet("color: #ffffff;")
        layout.addWidget(header)

        # Wallet address
        self.wallet_label = QLabel(self.wallet_address)
        self.wallet_label.setFont(QFont("SF Mono", 13))
        self.wallet_label.setStyleSheet("""
            color: #10b981;
            background-color: #0a0a0a;
            border-radius: 8px;
            padding: 12px 16px;
        """)
        self.wallet_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.wallet_label.setWordWrap(True)
        layout.addWidget(self.wallet_label)

        # Node ID
        node_label = QLabel(f"Node ID: {self.node_id}")
        node_label.setFont(QFont("SF Mono", 11))
        node_label.setStyleSheet("color: #6b7280;")
        self.node_id_label = node_label
        layout.addWidget(node_label)

        return card

    def create_status_card(self):
        """Network status card"""
        card = ModernCard()
        layout = QVBoxLayout(card)
        layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        header = QLabel("Network Status")
        header.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        header.setStyleSheet("color: #ffffff;")
        header_layout.addWidget(header)
        header_layout.addStretch()

        self.status_indicator = StatusIndicator("offline")
        header_layout.addWidget(self.status_indicator)

        layout.addLayout(header_layout)

        # Status items
        self.relay_status = self.create_status_row("NATS Relay", "Checking...")
        self.heartbeat_status = self.create_status_row("Heartbeats", "Inactive")
        self.rewards_status = self.create_status_row("Rewards", "Not Earning")

        layout.addWidget(self.relay_status)
        layout.addWidget(self.heartbeat_status)
        layout.addWidget(self.rewards_status)

        return card

    def create_contribution_card(self):
        """Contribution control card"""
        card = ModernCard()
        layout = QVBoxLayout(card)
        layout.setSpacing(16)

        # Header
        header = QLabel("Device Contribution")
        header.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        header.setStyleSheet("color: #ffffff;")
        layout.addWidget(header)

        # Description
        desc = QLabel("Enable contribution to earn VIBE rewards for sharing your resources with the network.")
        desc.setFont(QFont("SF Pro Display", 12))
        desc.setStyleSheet("color: #9ca3af;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Toggle button
        self.contribute_btn = QPushButton("Enable Contribution")
        self.contribute_btn.setMinimumHeight(50)
        self.contribute_btn.clicked.connect(self.toggle_contribution)
        layout.addWidget(self.contribute_btn)

        # Info
        info = QLabel("💰 Earn 0.1+ VIBE every 30 seconds")
        info.setFont(QFont("SF Pro Display", 12))
        info.setStyleSheet("color: #10b981;")
        layout.addWidget(info)

        return card

    def create_resources_card(self):
        """System resources card"""
        card = ModernCard()
        layout = QVBoxLayout(card)
        layout.setSpacing(16)

        # Header
        header = QLabel("System Resources")
        header.setFont(QFont("SF Pro Display", 16, QFont.Weight.Bold))
        header.setStyleSheet("color: #ffffff;")
        layout.addWidget(header)

        # Resources grid
        self.cpu_label = self.create_resource_row("CPU", "Detecting...")
        self.ram_label = self.create_resource_row("RAM", "Detecting...")
        self.storage_label = self.create_resource_row("Storage", "Detecting...")
        self.gpu_label = self.create_resource_row("GPU", "Detecting...")

        layout.addWidget(self.cpu_label)
        layout.addWidget(self.ram_label)
        layout.addWidget(self.storage_label)
        layout.addWidget(self.gpu_label)

        return card

    def create_status_row(self, label, value):
        """Create a status row"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        label_widget = QLabel(label)
        label_widget.setFont(QFont("SF Pro Display", 13))
        label_widget.setStyleSheet("color: #9ca3af;")

        value_widget = QLabel(value)
        value_widget.setFont(QFont("SF Mono", 13, QFont.Weight.Medium))
        value_widget.setStyleSheet("color: #ffffff;")
        value_widget.setObjectName(f"{label.lower().replace(' ', '_')}_value")

        layout.addWidget(label_widget)
        layout.addStretch()
        layout.addWidget(value_widget)

        return widget

    def create_resource_row(self, label, value):
        """Create a resource row"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        label_widget = QLabel(label)
        label_widget.setFont(QFont("SF Pro Display", 13))
        label_widget.setStyleSheet("color: #9ca3af;")

        value_widget = QLabel(value)
        value_widget.setFont(QFont("SF Mono", 13, QFont.Weight.Medium))
        value_widget.setStyleSheet("color: #ffffff;")
        value_widget.setObjectName(f"{label.lower()}_value")

        layout.addWidget(label_widget)
        layout.addStretch()
        layout.addWidget(value_widget)

        return widget

    def load_node_info(self):
        """Load node information from API"""
        try:
            response = httpx.get("http://localhost:8000/api/version", timeout=2.0)
            if response.status_code == 200:
                data = response.json()
                self.node_id = data.get("node_id", "Unknown")
                self.wallet_address = data.get("wallet_address", "Unknown")

                self.wallet_label.setText(self.wallet_address)
                self.node_id_label.setText(f"Node ID: {self.node_id}")
        except Exception as e:
            print(f"Failed to load node info: {e}")

    def load_contribution_settings(self):
        """Load contribution settings"""
        config_dir = Path.home() / ".apn"
        contrib_file = config_dir / "contribution_settings.json"

        if contrib_file.exists():
            try:
                with open(contrib_file, 'r') as f:
                    settings = json.load(f)
                    self.contribution_enabled = settings.get('enabled', False)
                    self.update_contribution_ui()
            except Exception as e:
                print(f"Failed to load settings: {e}")

    def toggle_contribution(self):
        """Toggle contribution on/off"""
        self.contribution_enabled = not self.contribution_enabled

        # Save settings
        config_dir = Path.home() / ".apn"
        config_dir.mkdir(parents=True, exist_ok=True)
        contrib_file = config_dir / "contribution_settings.json"

        settings = {
            "enabled": self.contribution_enabled,
            "relay_enabled": True,
            "compute_enabled": True,
            "storage_enabled": True
        }

        try:
            with open(contrib_file, 'w') as f:
                json.dump(settings, f, indent=2)

            # Update via API
            httpx.post(
                "http://localhost:8000/api/contribution/settings",
                json=settings,
                timeout=2.0
            )
        except Exception as e:
            print(f"Failed to save settings: {e}")

        self.update_contribution_ui()

    def update_contribution_ui(self):
        """Update contribution button state"""
        if self.contribution_enabled:
            self.contribute_btn.setText("✓ Contributing to Network")
            self.contribute_btn.setStyleSheet("""
                QPushButton {
                    background-color: #10b981;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 24px;
                    font-size: 14px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #059669;
                }
            """)
        else:
            self.contribute_btn.setText("Enable Contribution")
            self.contribute_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3b82f6;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 24px;
                    font-size: 14px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #2563eb;
                }
            """)

    def refresh_data(self):
        """Refresh all dynamic data"""
        # Retry loading wallet/node info if it failed on startup (race condition)
        if self.wallet_address in ("Loading...", "Unknown"):
            self.load_node_info()
        self.refresh_resources()
        self.refresh_status()

    def refresh_resources(self):
        """Refresh system resources"""
        if not PSUTIL_AVAILABLE:
            return

        try:
            # CPU
            cpu_count = psutil.cpu_count(logical=True)
            cpu_value = self.cpu_label.findChild(QLabel, "cpu_value")
            if cpu_value:
                cpu_value.setText(f"{cpu_count} cores")

            # RAM
            memory = psutil.virtual_memory()
            ram_gb = memory.total / (1024**3)
            ram_value = self.ram_label.findChild(QLabel, "ram_value")
            if ram_value:
                ram_value.setText(f"{ram_gb:.1f} GB")

            # Storage
            disk = psutil.disk_usage('/')
            storage_gb = disk.total / (1024**3)
            storage_value = self.storage_label.findChild(QLabel, "storage_value")
            if storage_value:
                storage_value.setText(f"{storage_gb:.0f} GB")

            # GPU
            gpu_value = self.gpu_label.findChild(QLabel, "gpu_value")
            if gpu_value:
                try:
                    import subprocess
                    result = subprocess.run(
                        ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                        capture_output=True, text=True, timeout=2
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        gpu_value.setText(result.stdout.strip())
                        gpu_value.setStyleSheet("color: #10b981; font-weight: 600;")
                    else:
                        gpu_value.setText("Not detected")
                except:
                    gpu_value.setText("Not detected")

        except Exception as e:
            print(f"Failed to refresh resources: {e}")

    def refresh_status(self):
        """Refresh network status"""
        try:
            response = httpx.get("http://localhost:8000/api/contribution/status", timeout=2.0)
            if response.status_code == 200:
                data = response.json()
                contribution = data.get('contribution', {})
                enabled = contribution.get('enabled', False)

                # Update status indicator
                if enabled:
                    self.status_indicator.update_status("online")

                    # Update relay status
                    relay_value = self.relay_status.findChild(QLabel, "nats_relay_value")
                    if relay_value:
                        relay_value.setText("Connected")
                        relay_value.setStyleSheet("color: #10b981; font-weight: 600;")

                    # Update heartbeat status
                    heartbeat_value = self.heartbeat_status.findChild(QLabel, "heartbeats_value")
                    if heartbeat_value:
                        heartbeat_value.setText("Active (30s)")
                        heartbeat_value.setStyleSheet("color: #10b981; font-weight: 600;")

                    # Update rewards status
                    rewards_value = self.rewards_status.findChild(QLabel, "rewards_value")
                    if rewards_value:
                        rewards_value.setText("Earning VIBE")
                        rewards_value.setStyleSheet("color: #10b981; font-weight: 600;")
                else:
                    self.status_indicator.update_status("offline")

                    relay_value = self.relay_status.findChild(QLabel, "nats_relay_value")
                    if relay_value:
                        relay_value.setText("Disconnected")
                        relay_value.setStyleSheet("color: #6b7280; font-weight: normal;")

                    heartbeat_value = self.heartbeat_status.findChild(QLabel, "heartbeats_value")
                    if heartbeat_value:
                        heartbeat_value.setText("Inactive")
                        heartbeat_value.setStyleSheet("color: #6b7280; font-weight: normal;")

                    rewards_value = self.rewards_status.findChild(QLabel, "rewards_value")
                    if rewards_value:
                        rewards_value.setText("Not Earning")
                        rewards_value.setStyleSheet("color: #6b7280; font-weight: normal;")

        except Exception as e:
            self.status_indicator.update_status("error")
            print(f"Failed to refresh status: {e}")


def main():
    """Run the modern UI"""
    app = QApplication(sys.argv)

    # Set application-wide font
    app.setFont(QFont("SF Pro Display", 12))

    window = APNModernUI()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
