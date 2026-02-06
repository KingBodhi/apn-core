from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QScrollArea,
                             QFrame, QGridLayout, QHBoxLayout)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont
import json

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

class NodesPage(QWidget):
    def __init__(self, config=None):
        super().__init__()
        self.config = config
        layout = QVBoxLayout()
        self.setLayout(layout)

        # Header
        header = QLabel("Alpha Protocol Network - Live Nodes")
        header.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Network Summary
        self.summary_label = QLabel("Pythia Master: 192.168.1.77 | Connected Nodes: 0")
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_label.setStyleSheet("color: #00ff88; font-size: 14px; padding: 10px;")
        layout.addWidget(self.summary_label)

        # Scroll area for nodes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        # Container for node cards
        self.nodes_container = QWidget()
        self.nodes_layout = QVBoxLayout()
        self.nodes_container.setLayout(self.nodes_layout)
        scroll.setWidget(self.nodes_container)
        layout.addWidget(scroll)

        # Status label
        self.status_label = QLabel("Loading network information...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-style: italic; padding: 20px;")
        self.nodes_layout.addWidget(self.status_label)

        # Auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.fetch_network_nodes)
        self.refresh_timer.start(5000)  # Refresh every 5 seconds

        # Initial fetch
        self.fetch_network_nodes()

    def fetch_network_nodes(self):
        """Fetch network nodes from Pythia Master API"""
        if not HTTPX_AVAILABLE:
            self.status_label.setText("httpx library not available. Install with: pip install httpx")
            return

        try:
            with httpx.Client(timeout=3.0) as client:
                response = client.get("http://192.168.1.77:8081/api/status")
                if response.status_code == 200:
                    data = response.json()
                    self.display_nodes(data.get('peers', []))
                else:
                    self.status_label.setText(f"API Error: HTTP {response.status_code}")
        except httpx.ConnectError:
            self.status_label.setText("Cannot connect to Pythia Master Node (192.168.1.77:8081)")
        except Exception as e:
            self.status_label.setText(f"Error fetching network data: {str(e)}")

    def display_nodes(self, peers):
        """Display network nodes with their resources"""
        # Clear existing nodes (except status label)
        while self.nodes_layout.count() > 1:
            item = self.nodes_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        if not peers:
            self.summary_label.setText("Pythia Master: 192.168.1.77 | Connected Nodes: 0")
            self.status_label.setText("No APN Core nodes connected yet. Waiting for workers to join...")
            return

        # Update summary
        self.summary_label.setText(f"Pythia Master: 192.168.1.77 | Connected Nodes: {len(peers)}")

        # Remove status label as we have data
        if self.status_label.parent():
            self.status_label.hide()

        # Create node cards
        for peer in peers:
            node_card = self.create_node_card(peer)
            self.nodes_layout.addWidget(node_card)

        # Add stretch at the end
        self.nodes_layout.addStretch()

    def create_node_card(self, peer):
        """Create a styled card widget for a node"""
        card = QFrame()
        card.setFrameShape(QFrame.Shape.Box)
        card.setStyleSheet("""
            QFrame {
                background-color: rgba(20, 20, 40, 0.8);
                border: 1px solid #00ff88;
                border-radius: 8px;
                padding: 15px;
                margin: 5px;
            }
        """)

        layout = QVBoxLayout()
        card.setLayout(layout)

        # Node header
        header_layout = QHBoxLayout()

        # Status indicator
        status_dot = QLabel("●")
        status_dot.setStyleSheet("color: #00ff88; font-size: 20px;")
        header_layout.addWidget(status_dot)

        # Node ID
        node_id = peer.get('node_id', 'Unknown')
        node_label = QLabel(f"<b>{node_id}</b>")
        node_label.setStyleSheet("color: #ffffff; font-size: 14px;")
        header_layout.addWidget(node_label)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        # Wallet address
        wallet = peer.get('wallet_address', 'N/A')
        wallet_label = QLabel(f"Wallet: {wallet[:20]}..." if len(wallet) > 20 else f"Wallet: {wallet}")
        wallet_label.setStyleSheet("color: #888; font-size: 11px; font-family: monospace;")
        layout.addWidget(wallet_label)

        # Resources section
        resources = peer.get('resources')
        if resources:
            resources_frame = QFrame()
            resources_frame.setStyleSheet("background-color: rgba(0, 255, 136, 0.1); border-radius: 5px; padding: 10px; margin-top: 10px;")
            res_layout = QGridLayout()
            resources_frame.setLayout(res_layout)

            # CPU
            cpu_cores = resources.get('cpu_cores', 0)
            res_layout.addWidget(QLabel("🔹 CPU:"), 0, 0)
            cpu_label = QLabel(f"<b>{cpu_cores} cores</b>")
            cpu_label.setStyleSheet("color: #00ff88;")
            res_layout.addWidget(cpu_label, 0, 1)

            # RAM
            ram_mb = resources.get('ram_mb', 0)
            ram_gb = ram_mb / 1024
            res_layout.addWidget(QLabel("🔹 RAM:"), 1, 0)
            ram_label = QLabel(f"<b>{ram_gb:.1f} GB</b>")
            ram_label.setStyleSheet("color: #00ff88;")
            res_layout.addWidget(ram_label, 1, 1)

            # Storage
            storage_gb = resources.get('storage_gb', 0)
            res_layout.addWidget(QLabel("🔹 Storage:"), 0, 2)
            storage_label = QLabel(f"<b>{storage_gb} GB</b>")
            storage_label.setStyleSheet("color: #00ff88;")
            res_layout.addWidget(storage_label, 0, 2)

            # GPU
            if resources.get('gpu_available') and resources.get('gpu_model'):
                gpu_model = resources.get('gpu_model', 'Unknown')
                res_layout.addWidget(QLabel("🔹 GPU:"), 1, 2)
                gpu_label = QLabel(f"<b>{gpu_model}</b>")
                gpu_label.setStyleSheet("color: #ffaa00;")
                res_layout.addWidget(gpu_label, 1, 3)

            layout.addWidget(resources_frame)
        else:
            no_res_label = QLabel("Resources: Not available")
            no_res_label.setStyleSheet("color: #888; font-style: italic;")
            layout.addWidget(no_res_label)

        # Capabilities
        capabilities = peer.get('capabilities', [])
        if capabilities:
            cap_text = "Capabilities: " + ", ".join(capabilities)
            cap_label = QLabel(cap_text)
            cap_label.setStyleSheet("color: #aaa; font-size: 10px; margin-top: 5px;")
            layout.addWidget(cap_label)

        return card

    def update_nodes(self, nodes):
        """Legacy compatibility method for Meshtastic nodes"""
        # This method is called by MainWindow from MeshtasticService
        # We're now using fetch_network_nodes instead
        pass
