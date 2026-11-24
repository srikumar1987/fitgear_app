from types import SimpleNamespace

from app.sizes import DEFAULT_SCALE, normalise_size, parse_paste, parse_player_list, sleeve_label, validate_order

TEAM_SHEET = (
    "Houston Spartans\n"
    "P.NO\tName\tName on Jersey\tNumber on Jersey\tT Shirt Size\tTshirt Sleeve\tTrack Size\n"
    "1\tDhanasekar R\tDHANA\t7\tL\tHalf\tL\n"
    "2\tMustaq Ahmed\tMUSTAQ\t10\t3xl\tFull Sleeve\t\n"
    "3\t\"Karthik\nKumar\"\tKARTHIK\t99\tM\tfull\tXL\n"
    "4\tLoki\t\t\t\t\tM\n"
)


def test_player_list_uses_jersey_number_track_size_and_sleeve():
    players = parse_player_list(TEAM_SHEET)
    assert [p["player_name"] for p in players] == ["DHANA", "MUSTAQ", "KARTHIK", "Loki"]
    assert [p["number"] for p in players] == ["7", "10", "99", ""]          # Number on Jersey, not P.NO
    assert [p["size"] for p in players] == ["L", "XXXL", "M", ""]
    assert [p["track_size"] for p in players] == ["L", "", "XL", "M"]
    assert [p["sleeve"] for p in players] == ["Half", "Full Sleeve", "full", ""]


def test_player_list_without_header_is_rejected():
    assert parse_player_list("Dhana\t7\tL") is None


def test_sleeve_label():
    assert [sleeve_label(s) for s in ["HALF", "Full Sleeve", "sleeve less", "", "3/4"]] == [
        "Half sleeve", "Full sleeve", "Sleeveless", "", "3/4 sleeve"]


def test_item_paste_picks_size_column_by_item():
    jersey = parse_paste(TEAM_SHEET)
    pants = parse_paste(TEAM_SHEET, bottom_wear=True)
    assert jersey[0] == {"player_name": "DHANA", "number": "7", "size": "L", "qty": 1}
    assert [r["size"] for r in pants] == ["L", "", "XL", "M"]
    assert jersey[2]["player_name"] == "KARTHIK"                            # quoted multi-line cell kept in one row


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
