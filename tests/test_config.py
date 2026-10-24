"""Settings load, migration and clean-up: old or hand-edited settings files must never break the app."""

import json

from laughclipper import config
from laughclipper.config import DEFAULTS, S


def load(data):
    config.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_PATH.write_text(json.dumps(data), encoding="utf-8")
    return config.load()


def test_first_run_has_no_settings_file():
    assert config.load() is False
    assert S == DEFAULTS


def test_defaults_are_not_shared_with_settings():
    S["triggers"].append("gasp")
    S["sound_levels"]["gasp"] = 0.5
    assert DEFAULTS["triggers"] == ["laughter"]
    assert DEFAULTS["sound_levels"] == {}


def test_v1_settings_keep_working():
    assert load({"threshold": 0.2, "hits_needed": 4, "silence_level": 0.01, "unknown": 1}) is True
    assert S["min_laugh"] == 2.0                      # hits_needed -> min_laugh
    assert S["silence_level"] == DEFAULTS["silence_level"]   # clamped for Wave Link's quiet mic
    assert "unknown" not in S
    # everything added later starts off the old behaviour
    assert S["triggers"] == ["laughter"]
    assert S["game_only"] is False
    assert S["medal_check"] is True
    assert S["sound_levels"] == {} and S["sound_sources"] == {}


def test_setup_guide_only_for_new_users():
    assert config.load() is False and S["setup_done"] is False     # brand new: show the guide
    load({"threshold": 0.2})
    assert S["setup_done"] is True                                 # already using the app: don't
    load({"setup_done": False})
    assert S["setup_done"] is False                                # closed mid-guide last time


def test_triggers_are_cleaned():
    load({"triggers": ["bogus", "gasp", "laughter", "gasp"]})
    assert S["triggers"] == ["laughter", "gasp"]
    load({"triggers": "laughter"})
    assert S["triggers"] == ["laughter"]
    load({"triggers": []})
    assert S["triggers"] == ["laughter"]


def test_games_are_cleaned():
    load({"games": ["C:/Games/RocketLeague.exe", {"exe": "ROCKETLEAGUE.EXE"}, {"exe": "Valorant.exe", "name": "VALORANT"},
                    5, {}, {"exe": "  "}],
          "ignored_games": ["Hades.exe", 3]})
    assert S["games"] == [{"exe": "rocketleague.exe", "name": "rocketleague"},
                          {"exe": "valorant.exe", "name": "VALORANT"}]
    assert S["ignored_games"] == ["hades.exe"]


def test_per_sound_settings_are_cleaned():
    load({"sound_levels": {"shouting": 0.5, "gasp": 5, "laughter": "x", "bogus": 0.3, "cheering": True},
          "sound_sources": {"shouting": ["mic", 3], "gasp": [], "bogus": ["mic"], "screaming": "mic"}})
    assert S["sound_levels"] == {"shouting": 0.5, "gasp": 0.9}
    assert S["sound_sources"] == {"shouting": ["mic"]}


def test_new_options_are_cleaned():
    load({"sound_volume": 250, "bookmark_hotkey": 5, "clips_folder": None, "sound_device": ["x"]})
    assert S["sound_volume"] == 100 and S["bookmark_hotkey"] == "" and S["clips_folder"] == ""
    assert S["sound_device"] == ""
    load({"long_laugh": 100})
    assert S["long_laugh"] == 30 and S["smart_length"] is False
    load({"long_laugh": "x"})
    assert S["long_laugh"] == DEFAULTS["long_laugh"]
    load({"sound_name": "airhorn"})
    assert S["sound_name"] == "chime"
    load({"sound_name": "marimba"})
    assert S["sound_name"] == "marimba"
    load({"sound_volume": "loud"})
    assert S["sound_volume"] == DEFAULTS["sound_volume"]
    assert S["bookmark_when_recording"] is False and S["sound_cue"] is False     # off until turned on


def test_save_and_load_round_trip():
    S["triggers"] = ["laughter", "cheering"]
    S["sound_levels"] = {"cheering": 0.4}
    config.save()
    S["triggers"] = ["gasp"]
    assert config.load() is True
    assert S["triggers"] == ["laughter", "cheering"]
    assert S["sound_levels"] == {"cheering": 0.4}
