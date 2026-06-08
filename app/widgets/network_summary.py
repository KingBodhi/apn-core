from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

class NetworkSummary(QWidget):
    """Stub NetworkSummary widget"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Network Summary"))
