"""'Only listen while playing': which windows count as games, the alt-tab grace, and auto-adding."""

import os

import pytest

from laughclipper import winutil
from laughclipper.config import S

RL = (11, r"F:\SteamLibrary\steamapps\common\rocketleague\Binaries\Win64\RocketLeague.exe", False)
DISCORD = (12, r"C:\Users\x\AppData\Local\Discord\Discord.exe", False)
HADES = (13, r"C:\Games\Hades\Hades.exe", True)                 # full-screen, not in any launcher folder


@pytest.mark.parametrize("path, game", [
    (RL[1], True),
    (r"C:\Program Files (x86)\Steam\steamapps\common\Rust\RustClient.exe", True),
    (r"C:\Program Files\Epic Games\Fortnite\FortniteGame\Binaries\Win64\FortniteClient-Win64-Shipping.exe", True),
    (r"C:\Riot Games\VALORANT\live\VALORANT.exe", True),
    (r"C:\XboxGames\Forza Horizon 5\Content\ForzaHorizon5.exe", True),
    (r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe", False),
    (r"C:\Riot Games\Riot Client\RiotClientServices.exe", False),
    (r"F:\SteamLibrary\steamapps\common\wallpaper_engine\wallpaper32.exe", False),
    (r"F:\SteamLibrary\steamapps\common\Some Game\UnityCrashHandler64.exe", False),
    (r"C:\Program Files (x86)\Steam\steamwebhelper.exe", False),
    (r"C:\Program Files\Mozilla Firefox\firefox.exe", False),
])
def test_library_games(path, game):
    assert winutil.is_library_game(path) is game


def test_off_means_always_listening(ctrl, pc):
    pc.front = DISCORD
    ctrl.check_game()
    assert ctrl.status() == "listening" and not ctrl.engine.gated


def test_waits_for_a_game_and_keeps_listening_after_alt_tab(ctrl, pc):
    S["auto_add_games"] = False
    S["games"] = [{"exe": "rocketleague.exe", "name": "Rocket League"}]
    pc.front = DISCORD
    ctrl.set("game_only", True)
    assert ctrl.status() == "waiting"
    pc.front = RL
    ctrl.check_game()
    assert ctrl.status() == "listening" and ctrl.game == "Rocket League"
    pc.front = DISCORD
    pc.wait(4)
    ctrl.check_game()
    assert ctrl.status() == "listening"                 # inside the 5 s grace
    pc.wait(2)
    ctrl.check_game()
    assert ctrl.status() == "waiting" and ctrl.game is None


def test_own_window_listens_so_the_meters_work(ctrl, pc):
    pc.front = DISCORD
    ctrl.set("game_only", True)
    assert ctrl.engine.gated
    pc.front = (os.getpid(), r"C:\LaughClipper\LaughClipper.exe", False)
    ctrl.check_game()
    assert not ctrl.engine.gated and ctrl.tuning and ctrl.game is None
    pc.front = DISCORD
    ctrl.check_game()
    assert ctrl.engine.gated and not ctrl.tuning


def test_fullscreen_counts_only_when_turned_on(ctrl, pc):
    pc.front = HADES
    ctrl.set("game_only", True)
    assert ctrl.game == "Hades"
    ctrl.set("fullscreen_games", False)
    pc.wait(10)
    ctrl.check_game()
    assert ctrl.engine.gated


def test_library_games_are_added_once_and_removal_sticks(ctrl, pc):
    pc.front = RL
    ctrl.set("game_only", True)
    ctrl.check_game()
    assert S["games"] == [{"exe": "rocketleague.exe", "name": "RocketLeague"}]
    S["games"] = []
    S["ignored_games"] = ["rocketleague.exe"]            # what Remove does
    pc.wait(10)
    ctrl.check_game()
    assert S["games"] == [] and ctrl.engine.gated


def test_pausing_overrides_everything(ctrl, pc):
    pc.front = RL
    ctrl.set("game_only", True)
    ctrl.set("running", False)
    assert ctrl.status() == "paused"
