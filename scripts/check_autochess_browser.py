#!/usr/bin/env python3
"""Run browser checks against disposable loopback accounts/services, never production."""
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_toy.test_autochess_web import Fixture, NODE, ROOT

if __name__ == '__main__':
    with Fixture() as fixture:
        # Fixture-only funds for a visible purchase/equip -> actual 3D room check.
        # The separate engine test earns real solo-NPC rewards without this fixture.
        seed = fixture.base / 'seed-wallet.ts'
        seed.write_text(f'''import {{CedarEconomy}} from {str(ROOT / 'vendor/autochess/lib/accounts/cedartoy-economy.ts')!r};
import {{platformIdentity}} from {str(ROOT / 'vendor/autochess/lib/autochess/platform-adapter.ts')!r};
const economy=new CedarEconomy(process.argv[2]);
const who=platformIdentity({{playerId:'101',actorType:'human'}});
economy.sync(who,[{{playerId:'101',name:'临时棋手',actorType:'human'}}]);
for(let i=0;i<3;i++)economy.settle({{roomId:'browser-fixture-'+i,round:3,entries:[{{accountId:who.owner.accountId,outcome:'win'}}]}});
economy.close();
''')
        subprocess.run([NODE, '--import', 'tsx', str(seed), str(fixture.base / 'rooms/economy')],
                       cwd=ROOT / 'vendor/autochess', check=True)
        result = subprocess.run(['node', str(Path(__file__).with_suffix('.cjs'))], cwd=Path(__file__).resolve().parents[1], env={
            **os.environ, 'AUTOCHESS_BROWSER_ORIGIN': fixture.origin,
            'AUTOCHESS_BROWSER_TOKEN': fixture.tokens['101'],
            'AUTOCHESS_BROWSER_FRIEND_TOKEN': fixture.tokens['303'],
        })
        raise SystemExit(result.returncode)
