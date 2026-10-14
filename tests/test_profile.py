import json

from carritos import profile
from carritos.core import Settings


def test_roundtrip(tmp_path):
    path = tmp_path / "sub" / "profile.json"
    settings = Settings(max_speed=400.0, trails=False)
    profile.save(path, ["carritos", "lab/cars"], {"carritos/x": {"name": "Zed", "color": (1, 2, 3)}}, settings)

    data = profile.load(path)
    assert data["channels"] == ["carritos", "lab/cars"]
    assert data["cars"] == {"carritos/x": {"name": "Zed", "color": (1, 2, 3)}}
    assert Settings.from_dict(data["settings"]) == settings


def test_missing_file_gives_empty_profile(tmp_path):
    assert profile.load(tmp_path / "nope.json") == {"channels": [], "cars": {}, "settings": {}}


def test_corrupt_or_wrong_shape_gives_empty_profile(tmp_path):
    for content in ("{not json", "[1, 2]", '"str"', ""):
        path = tmp_path / "p.json"
        path.write_text(content, encoding="utf-8")
        assert profile.load(path) == {"channels": [], "cars": {}, "settings": {}}


def test_load_drops_invalid_entries(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "channels": ["ok", "bad channel", 7, "ok", "a//b"],
        "cars": {
            "good": {"name": "N", "color": [1, 2, 3]},
            "badcolor": {"name": "N", "color": [999, 0, 0]},
            "notdict": 5,
            "longname": {"name": "x" * 100},
        },
        "settings": {"max_speed": 500},
    }), encoding="utf-8")

    data = profile.load(path)
    assert data["channels"] == ["ok"]
    assert data["cars"]["good"] == {"name": "N", "color": (1, 2, 3)}
    assert "color" not in data["cars"]["badcolor"]
    assert "notdict" not in data["cars"]
    assert len(data["cars"]["longname"]["name"]) <= 20
    assert data["settings"] == {"max_speed": 500}


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "profile.json"
    profile.save(path, ["a"], {}, Settings())
    profile.save(path, ["b"], {}, Settings())
    assert [p.name for p in tmp_path.iterdir()] == ["profile.json"]
    assert profile.load(path)["channels"] == ["b"]
