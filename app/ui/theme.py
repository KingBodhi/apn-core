"""
APN CORE - Modern Dark Theme with Holographic Effects
"""

class APNTheme:
    """Modern dark theme with holographic effects for APN CORE"""
    
    # Color Palette
    COLORS = {
        # Primary colors
        'alpha_gold': '#FFD700',
        'alpha_gold_hover': '#FFED4A',
        'alpha_gold_pressed': '#E6C200',
        
        # Background colors
        'bg_primary': '#0A0A0B',
        'bg_secondary': '#1A1A1D',
        'bg_tertiary': '#2D2D35',
        'bg_card': '#16161A',
        'bg_elevated': '#242429',
        
        # Text colors
        'text_primary': '#FFFFFF',
        'text_secondary': '#B8B8B8',
        'text_muted': '#7A7A7A',
        'text_accent': '#FFD700',
        
        # Border colors
        'border_primary': '#333338',
        'border_accent': '#FFD700',
        'border_hover': '#FFED4A',
        
        # Status colors
        'success': '#00FF88',
        'warning': '#FF8800',
        'error': '#FF4444',
        'info': '#44AAFF',
        
        # Holographic effects
        'glow_primary': 'rgba(255, 215, 0, 0.3)',
        'glow_secondary': 'rgba(255, 215, 0, 0.1)',
        'glass_primary': 'rgba(255, 255, 255, 0.05)',
        'glass_secondary': 'rgba(255, 255, 255, 0.02)',
    }
    
    @classmethod
    def get_main_stylesheet(cls):
        """Get the main application stylesheet"""
        return f"""
        /* Global Application Styles */
        QMainWindow {{
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 1,
                stop: 0 {cls.COLORS['bg_primary']},
                stop: 1 {cls.COLORS['bg_secondary']}
            );
            color: {cls.COLORS['text_primary']};
            font-family: 'SF Pro Display', 'Segoe UI', 'Roboto', sans-serif;
            font-size: 14px;
            font-weight: 400;
        }}
        
        /* Dock Widget (Sidebar) */
        QDockWidget {{
            background: {cls.COLORS['bg_card']};
            border: 1px solid {cls.COLORS['border_primary']};
            border-radius: 12px;
            margin: 8px;
        }}
        
        QDockWidget::title {{
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 0,
                stop: 0 {cls.COLORS['alpha_gold']},
                stop: 1 {cls.COLORS['alpha_gold_hover']}
            );
            color: {cls.COLORS['bg_primary']};
            padding: 12px;
            font-weight: 600;
            font-size: 16px;
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
            text-align: center;
        }}
        
        /* Navigation List */
        QListWidget {{
            background: transparent;
            border: none;
            outline: none;
            padding: 8px;
        }}
        
        QListWidget::item {{
            background: {cls.COLORS['glass_primary']};
            border: 1px solid {cls.COLORS['border_primary']};
            border-radius: 8px;
            padding: 16px 20px;
            margin: 4px 0px;
            font-size: 14px;
            font-weight: 500;
            color: {cls.COLORS['text_secondary']};
            transition: all 0.3s ease;
        }}
        
        QListWidget::item:hover {{
            background: {cls.COLORS['glass_primary']};
            border: 1px solid {cls.COLORS['border_hover']};
            color: {cls.COLORS['text_primary']};
        }}
        
        QListWidget::item:selected {{
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 0,
                stop: 0 {cls.COLORS['glow_primary']},
                stop: 1 {cls.COLORS['glow_secondary']}
            );
            border: 1px solid {cls.COLORS['alpha_gold']};
            color: {cls.COLORS['alpha_gold']};
            font-weight: 600;
        }}
        
        /* Holographic Buttons */
        QPushButton {{
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 1,
                stop: 0 {cls.COLORS['glass_primary']},
                stop: 1 {cls.COLORS['glass_secondary']}
            );
            border: 1px solid {cls.COLORS['border_primary']};
            border-radius: 8px;
            padding: 12px 24px;
            font-size: 14px;
            font-weight: 600;
            color: {cls.COLORS['text_primary']};
            min-height: 20px;
        }}
        
        QPushButton:hover {{
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 1,
                stop: 0 {cls.COLORS['alpha_gold']},
                stop: 0.3 {cls.COLORS['alpha_gold_hover']},
                stop: 1 {cls.COLORS['alpha_gold']}
            );
            border: 1px solid {cls.COLORS['alpha_gold_hover']};
            color: {cls.COLORS['bg_primary']};
        }}
        
        QPushButton:pressed {{
            background: {cls.COLORS['alpha_gold_pressed']};
            border: 1px solid {cls.COLORS['alpha_gold_pressed']};
            color: {cls.COLORS['bg_primary']};
        }}
        
        /* Primary Action Buttons */
        QPushButton[class="primary"] {{
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 1,
                stop: 0 {cls.COLORS['alpha_gold']},
                stop: 1 {cls.COLORS['alpha_gold_hover']}
            );
            border: 1px solid {cls.COLORS['alpha_gold']};
            color: {cls.COLORS['bg_primary']};
            font-weight: 700;
        }}
        
        QPushButton[class="primary"]:hover {{
            background: {cls.COLORS['alpha_gold_hover']};
        }}
        
        /* Glass Cards */
        QWidget[class="glass-card"] {{
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 1,
                stop: 0 {cls.COLORS['glass_primary']},
                stop: 1 {cls.COLORS['glass_secondary']}
            );
            border: 1px solid {cls.COLORS['border_primary']};
            border-radius: 16px;
            padding: 20px;
            margin: 8px;
        }}
        
        /* Input Fields */
        QLineEdit, QTextEdit, QComboBox {{
            background: {cls.COLORS['bg_elevated']};
            border: 1px solid {cls.COLORS['border_primary']};
            border-radius: 8px;
            padding: 12px;
            font-size: 14px;
            color: {cls.COLORS['text_primary']};
            selection-background-color: {cls.COLORS['alpha_gold']};
            selection-color: {cls.COLORS['bg_primary']};
        }}
        
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
            border: 2px solid {cls.COLORS['alpha_gold']};
        }}
        
        /* Labels */
        QLabel {{
            color: {cls.COLORS['text_primary']};
            font-size: 14px;
        }}
        
        QLabel[class="title"] {{
            font-size: 28px;
            font-weight: 700;
            color: {cls.COLORS['alpha_gold']};
            margin: 20px 0px;
        }}
        
        QLabel[class="heading"] {{
            font-size: 20px;
            font-weight: 600;
            color: {cls.COLORS['text_primary']};
            margin: 16px 0px 8px 0px;
        }}
        
        QLabel[class="subheading"] {{
            font-size: 16px;
            font-weight: 500;
            color: {cls.COLORS['text_secondary']};
            margin: 8px 0px;
        }}
        
        QLabel[class="status-online"] {{
            color: {cls.COLORS['success']};
            font-weight: 600;
        }}
        
        QLabel[class="status-offline"] {{
            color: {cls.COLORS['error']};
            font-weight: 600;
        }}
        
        /* Group Boxes */
        QGroupBox {{
            font-size: 16px;
            font-weight: 600;
            border: 1px solid {cls.COLORS['border_accent']};
            border-radius: 12px;
            margin-top: 12px;
            padding-top: 16px;
            color: {cls.COLORS['text_primary']};
            background: {cls.COLORS['glass_primary']};
        }}
        
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 8px 16px;
            color: {cls.COLORS['alpha_gold']};
            background: {cls.COLORS['bg_card']};
            border: 1px solid {cls.COLORS['border_accent']};
            border-radius: 8px;
            margin-left: 8px;
        }}
        
        /* Checkboxes */
        QCheckBox {{
            color: {cls.COLORS['text_primary']};
            font-size: 14px;
            spacing: 8px;
        }}
        
        QCheckBox::indicator {{
            width: 18px;
            height: 18px;
            border: 2px solid {cls.COLORS['border_primary']};
            border-radius: 4px;
            background: {cls.COLORS['bg_elevated']};
        }}
        
        QCheckBox::indicator:checked {{
            background: {cls.COLORS['alpha_gold']};
            border: 2px solid {cls.COLORS['alpha_gold']};
        }}
        
        QCheckBox::indicator:checked::after {{
            content: "✓";
            color: {cls.COLORS['bg_primary']};
            font-weight: bold;
        }}
        
        /* Scroll Areas */
        QScrollArea {{
            background: transparent;
            border: none;
        }}
        
        QScrollBar:vertical {{
            background: {cls.COLORS['bg_elevated']};
            width: 12px;
            border-radius: 6px;
            margin: 0px;
        }}
        
        QScrollBar::handle:vertical {{
            background: {cls.COLORS['alpha_gold']};
            min-height: 20px;
            border-radius: 6px;
        }}
        
        QScrollBar::handle:vertical:hover {{
            background: {cls.COLORS['alpha_gold_hover']};
        }}
        
        /* Status Indicators */
        QWidget[class="status-indicator"] {{
            border-radius: 6px;
            padding: 4px 12px;
            font-weight: 600;
            font-size: 12px;
        }}
        
        QWidget[class="status-online"] {{
            background: {cls.COLORS['success']};
            color: {cls.COLORS['bg_primary']};
        }}
        
        QWidget[class="status-offline"] {{
            background: {cls.COLORS['error']};
            color: white;
        }}
        
        QWidget[class="status-warning"] {{
            background: {cls.COLORS['warning']};
            color: {cls.COLORS['bg_primary']};
        }}
        """
    
    @classmethod
    def get_holographic_button_style(cls, variant="default"):
        """Get specific holographic button styles"""
        styles = {
            "primary": f"""
                QPushButton {{
                    background: qlineargradient(
                        x1: 0, y1: 0, x2: 1, y2: 1,
                        stop: 0 {cls.COLORS['alpha_gold']},
                        stop: 0.5 {cls.COLORS['alpha_gold_hover']},
                        stop: 1 {cls.COLORS['alpha_gold']}
                    );
                    border: 2px solid {cls.COLORS['alpha_gold']};
                    border-radius: 12px;
                    padding: 16px 32px;
                    font-size: 16px;
                    font-weight: 700;
                    color: {cls.COLORS['bg_primary']};
                }}
                QPushButton:hover {{
                    background: {cls.COLORS['alpha_gold_hover']};
                }}
            """,
            "secondary": f"""
                QPushButton {{
                    background: transparent;
                    border: 2px solid {cls.COLORS['alpha_gold']};
                    border-radius: 12px;
                    padding: 16px 32px;
                    font-size: 16px;
                    font-weight: 600;
                    color: {cls.COLORS['alpha_gold']};
                }}
                QPushButton:hover {{
                    background: {cls.COLORS['glass_primary']};
                }}
            """,
            "ghost": f"""
                QPushButton {{
                    background: {cls.COLORS['glass_secondary']};
                    border: 1px solid {cls.COLORS['border_primary']};
                    border-radius: 8px;
                    padding: 12px 24px;
                    font-size: 14px;
                    font-weight: 500;
                    color: {cls.COLORS['text_primary']};
                }}
                QPushButton:hover {{
                    background: {cls.COLORS['glass_primary']};
                    border: 1px solid {cls.COLORS['alpha_gold']};
                    color: {cls.COLORS['alpha_gold']};
                }}
            """
        }
        return styles.get(variant, styles.get("ghost", ""))
    
    @classmethod
    def get_card_style(cls, variant="default"):
        """Get glass card styles"""
        return f"""
            QWidget {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 {cls.COLORS['glass_primary']},
                    stop: 1 {cls.COLORS['glass_secondary']}
                );
                border: 1px solid {cls.COLORS['border_primary']};
                border-radius: 16px;
                padding: 24px;
                margin: 8px;
            }}
        """
