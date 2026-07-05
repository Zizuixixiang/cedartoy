from .base import VendorCmdError, VendorCmdGame


RUNNER_CODE = r'''
import json
import os
import sys

payload = json.load(sys.stdin)
save_dir = payload["save_dir"]
vendor_dir = payload["vendor_dir"]
command = payload.get("command") or "help"

sys.path.insert(0, vendor_dir)
import arcade
import blackjack
import roulette
import slots

arcade._SAVE = os.path.join(save_dir, "arcade_save.json")
slots._SAVE = os.path.join(save_dir, "slots_save.json")
blackjack._SAVE = os.path.join(save_dir, "blackjack_save.json")
roulette._SAVE = os.path.join(save_dir, "roulette_save.json")

if payload.get("reset"):
    for name in ("arcade_save.json", "slots_save.json", "blackjack_save.json", "roulette_save.json"):
        try:
            os.remove(os.path.join(save_dir, name))
        except FileNotFoundError:
            pass

print(arcade.cmd(command), end="")
'''


GAME = VendorCmdGame("arcade", "vendor/claude-arcade", RUNNER_CODE)


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "arcade_new"}:
        text = GAME.run(player_id, arguments.get("command") or "enter", reset=True)
    elif action in {"cmd", "arcade_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 arcade action")
    return {"game": "arcade", "player_id": player_id, "text": text}
