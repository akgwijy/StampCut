from stampcut.gui.url_panel import UrlPanel


def test_urls_and_invalid_lines(qtbot):
    p = UrlPanel()
    qtbot.addWidget(p)
    p.urls_edit.setPlainText("https://youtu.be/i3_SYn3e_kY\n\nnot a url\n  POZWcyKFvjY ")
    assert p.urls() == ["https://youtu.be/i3_SYn3e_kY", "not a url", "POZWcyKFvjY"]
    assert p.invalid_lines() == [2]
    assert p.highlight_invalid() is True
    assert len(p.urls_edit.extraSelections()) == 1
    p.urls_edit.setPlainText("https://youtu.be/i3_SYn3e_kY")
    assert p.highlight_invalid() is False
    assert p.urls_edit.extraSelections() == []


def test_analyze_signal_title_and_busy(qtbot):
    p = UrlPanel()
    qtbot.addWidget(p)
    with qtbot.waitSignal(p.analyze_requested, timeout=1000):
        p.analyze_btn.click()
    p.set_title("26.08.20 문성FC 하이라이트")
    assert p.title() == "26.08.20 문성FC 하이라이트"
    p.set_busy(True)
    assert not p.analyze_btn.isEnabled() and p.urls_edit.isReadOnly()
    p.set_busy(False)
    assert p.analyze_btn.isEnabled() and not p.urls_edit.isReadOnly()


def test_vertical_stacking(qtbot):
    w = UrlPanel()
    qtbot.addWidget(w)
    w.layout().activate()
    # URL 입력이 위, 타이틀이 아래, 분석 버튼은 타이틀 오른쪽
    assert w.urls_edit.geometry().bottom() < w.title_edit.geometry().top()
    assert w.analyze_btn.geometry().left() > w.title_edit.geometry().left()
    assert abs(w.analyze_btn.geometry().center().y() - w.title_edit.geometry().center().y()) < 20
