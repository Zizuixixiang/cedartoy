from .base import VendorCmdError, VendorCmdGame


RUNNER_CODE = r'''
import json
import os
import sys

payload = json.load(sys.stdin)
save_dir = payload["save_dir"]
vendor_dir = payload["vendor_dir"]
command = payload.get("command") or "status"

sys.path.insert(0, vendor_dir)
os.chdir(save_dir)
import leek

if payload.get("reset"):
    for name in ("leek_save.json", "leek_save.json.tmp", "leek_save.json.bak"):
        try:
            os.remove(os.path.join(save_dir, name))
        except FileNotFoundError:
            pass

print(leek.cmd(command), end="")
'''


GAME = VendorCmdGame("leek", "vendor/leek", RUNNER_CODE)


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "leek_new"}:
        seed = arguments.get("seed")
        career = str(arguments.get("career") or "").strip().lower()
        parts = ["new_game"]
        if career == "fund":
            parts.append("fund")
        if seed is not None:
            parts.append(str(seed))
        text = GAME.run(player_id, " ".join(parts), reset=True)
    elif action in {"cmd", "leek_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 leek action")
    return {"game": "leek", "player_id": player_id, "text": text}
