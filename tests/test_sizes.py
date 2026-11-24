from types import SimpleNamespace

from app.sizes import DEFAULT_SCALE, normalise_size, parse_paste, validate_order


def test_normalise_size():
    assert normalise_size("3xl") == "XXXL"
    assert normalise_size(" x l ") == "XL"
    assert normalise_size("Medium") == "M"


def test_parse_paste_skips_team_name_and_maps_headers():
    text = (
        "Vintage Warriors\t\t\t\n"
        "S.No\tName\tName on Jersey\tP.No (link)\tSize\tSleeve\n"
        "1\tDhanasekar\tDHANA\t2\tL\tHalf\n"
        "2\tMustaq\tMUSTAQ\t2.0\t3XL\tFull\n"
        "\t\t\t\t\t\n"
    )
    rows = parse_paste(text)
    assert rows == [
        {"player_name": "DHANA", "number": "2", "size": "L", "qty": 1},
        {"player_name": "MUSTAQ", "number": "2", "size": "XXXL", "qty": 1},
    ]


def test_parse_paste_without_header_uses_positions():
    rows = parse_paste("Varun,9,M,2\nHarsha,9,xl")
    assert rows[0] == {"player_name": "Varun", "number": "9", "size": "M", "qty": 2}
    assert rows[1]["size"] == "XL" and rows[1]["qty"] == 1


def _item(name, rows):
    return SimpleNamespace(name=name, sizes=[SimpleNamespace(**r) for r in rows])


def test_validation_warnings():
    jersey = _item("Jersey, half sleeve", [
        {"player_name": "Varun", "number": "9", "size": "M", "qty": 1},
        {"player_name": "Harsha", "number": "9", "size": "L", "qty": 1},
        {"player_name": "Karthik", "number": "999", "size": "", "qty": 1},
    ])
    pants = _item("Track pants", [{"player_name": "Varun", "number": "9", "size": "M", "qty": 1}])
    warnings = validate_order(SimpleNamespace(items=[jersey, pants]), DEFAULT_SCALE)
    text = " | ".join(warnings)
    assert "#9 (Varun, Harsha)" in text
    assert "Karthik (999)" in text
    assert "Missing size: Karthik" in text
    assert "Pant count (1) does not match jersey count (3)" in text
