from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel

class BlockTracker(QWidget):
    """Stub BlockTracker widget"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Block Tracker"))
