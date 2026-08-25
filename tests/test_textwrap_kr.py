from stampcut.core.textwrap_kr import text_width, wrap


def test_width_korean_vs_latin():
    assert text_width("원더골", 60) == 180
    assert abs(text_width("ab", 60) - 66) < 1e-9


def test_default_title_fits_one_line():
    assert text_width("26.08.20 문성FC 하이라이트", 64) < 960
    assert "\n" not in wrap("26.08.20 문성FC 하이라이트", 64)


def test_wrap_breaks_long_word_by_chars():
    assert wrap("가" * 20, 60) == "가" * 16 + "\n" + "가" * 4


def test_wrap_prefers_word_boundaries():
    assert wrap("오프사이드 기가막히게 거네", 60) == "오프사이드 기가막히게 거네"
    # 폭: 5+.55+5+.55+2+.55+2 = 15.65자 × 60 = 939 ≤ 960 → "정말"까지 1행, "대박"만 2행
    assert wrap("오프사이드 기가막히게 거네 정말 대박", 60) == "오프사이드 기가막히게 거네 정말\n대박"
    assert wrap("오프사이드 기가막히게 거네 정말 대박이다", 60) == "오프사이드 기가막히게 거네 정말\n대박이다"


def test_wrap_truncates_with_ellipsis():
    out = wrap("가" * 40, 60)
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[1].endswith("…") and text_width(lines[1], 60) <= 960
    assert lines[1] == "가" * 15 + "…"


def test_wrap_empty():
    assert wrap("", 60) == ""
