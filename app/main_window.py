from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QMainWindow, QDockWidget, QListWidget, QStackedWidget, QSizePolicy
from PyQt6.QtGui import QFont
from app.pages.home_page import HomePage
from app.pages.apn_page import APNPage
from app.pages.nodes_page import NodesPage
from app.ui.theme import APNTheme

class MainWindow(QMainWindow):
    def __init__(self, config=None):
        super().__init__()
        self.config = config
        version = getattr(config, 'version', '2.0.0') if config else '2.0.0'
        self.setWindowTitle(f"APN Core v{version} - Alpha Protocol Network")

        # Set minimum and default window size
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)

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

        # Minimal navigation items
        nav_items = [
            "🏠 Dashboard",
            "⚙️ Node Config",
            "🔗 Peer Nodes"
        ]
        self.drawer_list.addItems(nav_items)
        self.drawer_list.currentRowChanged.connect(self.navigate)
        self.drawer.setWidget(self.drawer_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.drawer)

        # Set drawer dimensions
        self.drawer.setMinimumWidth(200)
        self.drawer.setMaximumWidth(240)

        # Pages
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCentralWidget(self.stack)

        # Initialize pages with config
        self.home_page = HomePage(config)
        self.apn_page = APNPage(config)
        self.nodes_page = NodesPage(config)

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.apn_page)
        self.stack.addWidget(self.nodes_page)

        # Set up periodic updates
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_dashboard)
        self.update_timer.start(5000)  # Update every 5 seconds

        # Initialize to home page
        self.drawer_list.setCurrentRow(0)

    def start_service(self):
        """Stub for compatibility"""
        pass

    def navigate(self, index):
        """Navigate to selected page"""
        self.stack.setCurrentIndex(index)

    def update_dashboard(self):
        """Update dashboard with latest data"""
        pass
