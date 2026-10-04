// Public-state fixtures shared by the event planner and browser acceptance.
module.exports = function eventCases(base) {
  const make = (label, edit) => {
    const before = structuredClone(base);
    before.room_id = `events-${base.participants.length}-${label}`; before.revision = 700;
    const s = before.board_state;
    s.action_seq = 70; s.phase = 'manage'; s.last_card_events = []; s.last_action_note = '';
    s.current_player_id = 'human:2'; s.turn_player_id = 'human:2'; s.dice = [];
    before.participants.forEach(p => {const i=Number(p.player_id.split(':')[1])-1;p.display_name = ['南杉', '许知衡', '乔麦', '朋友四', '朋友五', '系统小机六'][i]; if(p.player_id==='human:2')p.participant_kind='system_npc';});
    s.players.forEach(p => {p.name = before.participants.find(x=>x.player_id===p.player_id).display_name; p.position=0;p.cash=1500;p.bankrupt=false;p.jailed=false;});
    s.tiles.forEach(t => {t.owner=null;t.level=0;t.mortgaged=false;});
    s.tiles[1].name='林街';s.tiles[1].price=160;s.tiles[3].name='花街';
    edit(s, null);
    const after = structuredClone(before); after.revision++; after.board_state.action_seq++;
    edit(before.board_state, after.board_state);
    return {label,before,after};
  };
  const p = (s,id='human:2') => s.players.find(p=>p.player_id===id);
  return [
    make('buy',(a,b)=>{if(!b){a.phase='purchase';p(a).position=1;}else{b.tiles[1].owner='human:2';p(b).cash-=160;b.last_action_note=`${p(b).name}以160买下林街。`;}}),
    make('rent',(a,b)=>{if(!b)a.tiles[1].owner='human:1';else{p(b).position=1;p(b).cash-=24;p(b,'human:1').cash+=24;b.last_action_note=`${p(b).name}支付24（林街租金）。`;}}),
    ...['chance','chest'].map(deck=>make(deck,(a,b)=>{if(b){p(b).cash+=50;b.last_card_events=[{event_id:'71:1',action_seq:71,player_id:'human:2',deck,text:'银行赠款50',summary:'现金 +50'}];}})),
    make('jail',(a,b)=>{if(b){p(b).position=10;p(b).jailed=true;}}),
    make('tax',(a,b)=>{if(b){p(b).position=4;p(b).cash-=200;b.last_action_note=`${p(b).name}支付200（所得税）。`;}}),
    make('bail',(a,b)=>{if(!b)p(a).jailed=true;else{p(b).jailed=false;p(b).cash-=50;b.last_action_note='已出狱，可以掷骰。';}}),
    make('build',(a,b)=>{if(!b){a.tiles[3].owner='human:2';a.tiles[3].level=1;}else b.tiles[3].level=2;}),
    make('sell',(a,b)=>{if(!b){a.tiles[3].owner='human:2';a.tiles[3].level=2;}else b.tiles[3].level=1;}),
    make('mortgage',(a,b)=>{if(!b)a.tiles[1].owner='human:2';else b.tiles[1].mortgaged=true;}),
    make('redeem',(a,b)=>{if(!b){a.tiles[1].owner='human:2';a.tiles[1].mortgaged=true;}else b.tiles[1].mortgaged=false;}),
    make('auction',(a,b)=>{if(!b)a.auction={tile_id:1,highest_bidder:'human:2',bid:220};else{b.auction=null;b.tiles[1].owner='human:2';p(b).cash-=220;b.last_action_note=`${p(b).name}以220买下林街。`;}}),
    make('trade',(a,b)=>{if(!b){a.tiles[1].owner='human:1';a.tiles[3].owner='human:2';a.trade={from:'human:1',to:'human:2',give_cash:30,take_cash:0,give_tiles:[1],take_tiles:[3]};}else{b.trade=null;b.tiles[1].owner='human:2';b.tiles[3].owner='human:1';b.last_action_note='交易已同时交割。';}}),
    make('bankrupt',(a,b)=>{if(!b){a.tiles[3].owner='human:2';a.tiles[3].level=3;}else{p(b).bankrupt=true;p(b).cash=0;b.tiles[3].owner='human:1';b.tiles[3].level=0;}}),
    make('salary',(a,b)=>{if(b){p(b).position=3;p(b).cash+=200;b.last_action_note='经过起点，领取200。';}}),
    make('multi',(a,b)=>{if(!b){a.phase='roll';a.current_player_id='human:1';a.tiles[1].owner='human:2';}else{p(b,'human:1').position=1;b.dice=[3,4];b.last_card_events=[{event_id:'71:1',action_seq:71,player_id:'human:1',deck:'chance',text:'前往林街',summary:'前往林街'}];b.last_action_note=`${p(b,'human:1').name}支付24（林街租金）。`;}}),
    make('roll',(a,b)=>{if(!b)a.phase='roll';else{b.dice=[3,4];p(b).position=7;b.last_action_note=`${p(b).name}掷出3+4。停在机会。`;}}),
  ];
};
