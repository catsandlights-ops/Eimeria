"""Launcher (also the entry point of Eimeria.exe).

    Eimeria.exe                         start the app
    Eimeria.exe --apply-update ...      install an update (run by the updater from the NEW version's folder)
    Eimeria.exe --smoke-test FILE       build check: open the whole app invisibly, write "ok <version>" to FILE
"""
import sys


def smoke_test(out_file: str) -> int:
    """Used by the Windows build: proves the packaged app starts and builds its whole window."""
    import os
    import tempfile
    home = tempfile.mkdtemp(prefix="eimeria_smoke_")
    os.environ["EIMERIA_HOME"] = home                # test data goes to a temp folder, never into the build
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # Linux test machines have no screen
    try:
        from PySide6.QtWidgets import QApplication

        from app import config
        from app.gui.main_window import MainWindow
        from app.main import AppContext
        from app.version import VERSION
        import importlib
        for mod in ("app.ai_eval", "app.backtest", "app.selftest", "app.swing_data", "app.updater",
                    "app.sec_history", "app.market_timing", "app.apply_update"):
            importlib.import_module(mod)             # every module must be packaged in the .exe

        config.load_keys = lambda: ("", "")          # no keys: the app must still open (shows Settings)
        qapp = QApplication(sys.argv[:1])
        win = MainWindow(AppContext())
        win.show()
        qapp.processEvents()
        tabs = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        win.close()
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(f"ok {VERSION} tabs={','.join(tabs)}\n")
        return 0
    except Exception:
        import traceback
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("FAILED\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--apply-update":
        from app import apply_update
        sys.exit(apply_update.main(sys.argv[2:]))
    if len(sys.argv) > 2 and sys.argv[1] == "--smoke-test":
        sys.exit(smoke_test(sys.argv[2]))
    from app.main import main
    sys.exit(main())
