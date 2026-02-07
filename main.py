"""
APN CORE - Main Application Entry Point
Alpha Protocol Network - Modern GUI

Version: 2.0.0-minimal
"""
import sys
import asyncio
import threading
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Core APN imports
from core.logging_config import setup_logging, get_logger
from core.settings import get_settings

# Modern GUI
from app.modern_ui import APNModernUI

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
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger("main")

    logger.info("Starting APN CORE v2.0.0-minimal")
    logger.info("Alpha Protocol Network - Modern Client")

    try:
        # Start APN Core server in background thread
        logger.info("Starting APN Core server on port 8000...")
        apn_server_thread = threading.Thread(target=start_apn_server, daemon=True)
        apn_server_thread.start()
        logger.info("APN Core server started in background")

        # Start PyQt UI
        app = QApplication(sys.argv)

        # Modern UI with dark theme
        window = APNModernUI()
        window.setWindowTitle("APN Core v2.0.0")
        window.show()

        # Start the GUI event loop
        exit_code = app.exec()
        sys.exit(exit_code)

    except Exception as e:
        logger.error(f"Application startup error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
