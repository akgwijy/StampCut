from stampcut.gui.main_window import MainWindow


def test_main_window_constructs(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    assert w.windowTitle().startswith("StampCut")
