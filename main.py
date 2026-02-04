"""
APN Core Dashboard - Main Application Entry Point
Alpha Protocol Network GUI with integrated mesh networking.

Version: 1.0.0
"""
import sys
import asyncio
import threading
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Core APN imports
from core.config import APNConfig, APN_CORE_VERSION
from core.logging_config import setup_logging
from core.service_manager import ServiceManager

# GUI imports
from app.main_window import MainWindow

# Legacy compatibility
from app.pages import globals

# Global service manager instance
service_manager = None

# APN Server thread
apn_server_thread = None

def start_apn_server():
    """Start the APN Core server in a background thread"""
    import uvicorn
    from apn_server import app

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        access_log=False
    )
    server = uvicorn.Server(config)

    # Run in the thread's event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())

def main():
    """Main application entry point"""
    global apn_server_thread

    # Setup logging
    logger = setup_logging("INFO")
    logger.info(f"Starting APN Core Dashboard v{APN_CORE_VERSION}")
    logger.info("Alpha Protocol Network - Sovereign Mesh Node")

    try:
        # Load configuration
        config = APNConfig.load()
        logger.info(f"Loaded configuration for node: {config.identity.node_id}")

        # Start APN Core server in background thread
        logger.info("Starting APN Core server on port 8000...")
        apn_server_thread = threading.Thread(target=start_apn_server, daemon=True)
        apn_server_thread.start()
        logger.info("APN Core server started in background")

        # Fix for QWebEngineView
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

        # Start PyQt UI
        app = QApplication(sys.argv)
        window = MainWindow(config)
        window.setWindowTitle(f"APN Core Dashboard v{APN_CORE_VERSION}")
        window.show()

        # Start services after window is shown (like original)
        window.start_service()

        # Start the GUI event loop
        exit_code = app.exec()
        sys.exit(exit_code)

    except Exception as e:
        logger.error(f"Application startup error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
