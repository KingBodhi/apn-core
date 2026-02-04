from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QMainWindow, QDockWidget, QListWidget, QStackedWidget, QSizePolicy
from PyQt6.QtGui import QFont
from app.pages.home_page import HomePage
from app.pages.apn_page import APNPage
from app.pages.chat_page import ChatPage
from app.pages.map_page import MapPage
from app.pages.nodes_page import NodesPage
from app.pages.profile_page import ProfilePage
from app.pages.devices_page import DevicesPage
from app.ui.theme import APNTheme
from app.ui.components import HolographicHeader
from services.meshtastic_service import MeshtasticService

class MainWindow(QMainWindow):
    def __init__(self, config=None):
        super().__init__()
        self.config = config
        version = getattr(config, 'version', '1.0.0') if config else '1.0.0'
        self.setWindowTitle(f"APN Core Dashboard v{version} - Alpha Protocol Network")
        
        # Set minimum and default window size
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)
        
        # Enable window resizing
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowMinimizeButtonHint)
        
        # Apply the modern holographic theme
        self.setStyleSheet(APNTheme.get_main_stylesheet())
        
        # Set application font with fallbacks
        font = QFont()
        font.setFamily("SF Pro Display, Segoe UI, Arial, sans-serif")
        font.setPointSize(10)
        self.setFont(font)

        # Navigation Drawer
        self.drawer = QDockWidget("APN Navigation", self)
        self.drawer.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.drawer_list = QListWidget()
        
        # Modern navigation items with better icons
        nav_items = [
            "🏠 Dashboard", 
            "📱 Devices", 
            "⚙️ Node Config", 
            "💬 Mesh Chat", 
            "🗺️ Network Map", 
            "🔗 Peer Nodes", 
            "👤 Identity"
        ]
        self.drawer_list.addItems(nav_items)
        self.drawer_list.currentRowChanged.connect(self.navigate)
        self.drawer.setWidget(self.drawer_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.drawer)
        
        # Set drawer dimensions
        self.drawer.setMinimumWidth(240)
        self.drawer.setMaximumWidth(280)

        # Pages
        self.stack = QStackedWidget()
        # Ensure stack widget can expand properly
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCentralWidget(self.stack)

        # Initialize pages with config
        self.home_page = HomePage(config)
        self.devices_page = DevicesPage(config)
        self.apn_page = APNPage(config)
        self.chat_page = ChatPage(config)
        self.map_page = MapPage(config)
        self.nodes_page = NodesPage(config)
        self.profile_page = ProfilePage(config)

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.devices_page)
        self.stack.addWidget(self.apn_page)
        self.stack.addWidget(self.chat_page)
        self.stack.addWidget(self.map_page)
        self.stack.addWidget(self.nodes_page)
        self.stack.addWidget(self.profile_page)

        # Meshtastic Service (like original working version)
        self.service = MeshtasticService()
        self.service.new_message.connect(self.chat_page.append_message)
        self.service.update_nodes.connect(self.update_nodes_all)

        # Set up periodic updates
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_dashboard)
        self.update_timer.start(5000)  # Update every 5 seconds

        # Initialize to home page
        self.drawer_list.setCurrentRow(0)

    def start_service(self):
        """Call this AFTER the window is shown to avoid QWidget initialization errors."""
        self.service.start()

    def navigate(self, index):
        """Navigate to selected page"""
        self.stack.setCurrentIndex(index)

    def update_dashboard(self):
        """Update dashboard with latest data from service manager"""
        try:
            from app.pages import globals
            if hasattr(globals, 'service_manager') and globals.service_manager:
                # This will be called periodically to refresh UI
                pass
        except Exception as e:
            print(f"Dashboard update error: {e}")

    def update_nodes_all(self, nodes):
        """Update all pages with node data"""
        if hasattr(self.home_page, 'update_nodes'):
            self.home_page.update_nodes(nodes)
        if hasattr(self.map_page, 'update_nodes'):
            self.map_page.update_nodes(nodes)
        if hasattr(self.nodes_page, 'update_nodes'):
            self.nodes_page.update_nodes(nodes)
