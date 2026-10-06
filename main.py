import sys
import os
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from ui.fonts import apply_application_font, register_fonts
from ui.main_window import MainWindow
from ui.theme import APP_STYLESHEET


def main():
    app = QApplication(sys.argv)

    # 字体必须在 QApplication 之后、应用样式表之前注册
    register_fonts()
    apply_application_font(app)

    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "assets", "app_icon.png")
    app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    window.show()

    # 首次启动向导（PRODUCT_SPEC 4.7）。只在首次出现；每步可跳过，跳过即用默认值。
    # 主窗口先 show()：向导是模态框，必须已有运行中的事件循环（见 ui/onboarding.py）。
    if not window.database.is_onboarded():
        from ui.onboarding import OnboardingDialog

        OnboardingDialog(window.database, window).exec()
        # 无论"完成"还是直接关掉，都视为已处理——向导绝不能变成每次启动的门槛
        window.database.mark_onboarded()
        window.refresh_all()

    # 完成后进入 P0，任务输入框获得焦点（4.7）
    window.focus_today_input()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()