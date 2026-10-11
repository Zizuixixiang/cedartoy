'use strict';
// Shared synthetic old-account records exercised by jsdom and real Chromium.
// No production account, token, network request or database is used here.
async function checkHistoryTitles() {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const originalFetch = window.fetch;
  const legacy = {mbti:'MBTI',enneagram:'九型人格',dnd:'九阵营',love:'爱之语',ecr:'依恋类型',humanity:'人类浓度检测',sins_virtues:'七宗罪 VS 七美德',bdsmtest:'BDSMTest'};
  const saves = Object.fromEntries(researchScales.map(([id]) => [id, {slots:[1,2,3,4,5].map(slot => ({slot,latest_result:'research',completed_at:'2026-09-01 12:34:56'}))}]));
  for (const id of Object.keys(legacy)) saves[id] = {latest_result:'old-result',completed_at:'2026-09-01'};
  const persisted = JSON.stringify(saves);
  const rounds = deeptalkTopics.map(([,tid,title],i) => ({round_id:'old-round-'+tid,title,human_name:'旧人类',ai_name:'旧小机',status:i%2?'complete':'in_progress',updated_at:'2026-09-01'}));
  historyState = {activeId:'self',data:{self:{username:'旧账号',saves,deeptalk_history:{rounds:rounds.slice(0,2),total:10}},machines:[{username:'小机',saves}]}};
  const container = document.getElementById('historyContent');
  container.innerHTML = renderHistory(); bindHistoryTabs();
  function verifyCards() {
    const cards = [...container.querySelectorAll('.history-card')];
    for (const [id,title] of researchScales) {
      const card = cards.find(n=>n.querySelector('.history-card-title').textContent===title);
      check(card, id+' uses display title in old account history');
      check(card.querySelector('.history-card-desc').textContent==='测试进度与最近结果。',id+' readable description');
      check(card.querySelectorAll('.history-card-row').length===5,id+' all save slots retained');
      [...card.querySelectorAll('.history-card-row')].forEach((row,i)=>{
        const values=[...row.querySelectorAll('.history-stat b')].map(n=>n.textContent);
        check(values.join('|')===`${i+1}|已完成|2026-09-01`,id+' slot/result/date preserved');
      });
      check(!card.textContent.includes(id) && !card.textContent.includes('research'),id+' no internal label');
    }
    for (const [id,title] of Object.entries(legacy)) {
      check(saveGameNames[id]===title,id+' original name preserved');
      check(cards.some(n=>n.querySelector('.history-card-title').textContent===title && n.textContent.includes('old-result')),id+' original result preserved');
    }
  }
  try {
    verifyCards();
    let requests=0;
    window.fetch = async url => {
      check(String(url)==='/api/deeptalk?action=history&offset=2','history pagination offset'); requests++;
      return {ok:true,json:async()=>({rounds:rounds.slice(2),total:10})};
    };
    document.getElementById('deeptalkHistoryMore').click();
    await new Promise(resolve=>setTimeout(resolve,0));
    check(requests===1 && !document.getElementById('deeptalkHistoryMore'),'pagination consumes remaining rounds once');
    check([...container.querySelectorAll('.history-duel-game')].map(n=>n.textContent).join('|')===rounds.map(r=>r.title).join('|'),'all ten paged round display titles');
    verifyCards();
    container.querySelector('[data-history-tab="machine-0"]').click(); verifyCards();
    check(!container.querySelector('.history-duel-game'),'machine tab must not inherit human rounds');
    container.querySelector('[data-history-tab="self"]').click(); verifyCards();
    check(JSON.stringify(saves)===persisted,'rendering must not modify old result payloads');
    const hostile='<img src=x onerror="window.historyXss=1">';
    const probe=document.createElement('div');
    probe.innerHTML=renderHistoryGameCard('ipip_adhd',{slots:[{slot:hostile,latest_result:hostile,completed_at:hostile,achievements_list:[hostile]}]})
      +renderHistoryGameCard(hostile,{latest_result:hostile})
      +renderDeeptalkHistory({rounds:[{...rounds[0],title:hostile,human_name:hostile,ai_name:hostile,round_id:'\" onclick=alert(1)'}]});
    check(!probe.querySelector('img,script,[onerror],[onclick]') && !window.historyXss,'history data is escaped');
    check(probe.textContent.includes(hostile),'hostile text remains literal');
    document.querySelector('[data-filter="test"]').click();
    const search=document.getElementById('searchInput');
    for (const [id,title] of [...researchScales.map(([id,title])=>[id,title]),['ipip_adhd','ADHD'],['ipip_adhd','adhd']]) {
      search.value=title; search.dispatchEvent(new Event('input'));
      const visible=[...document.querySelectorAll('.game-card:not(.hidden)')];
      check(visible.length===1 && visible[0].dataset.game===id,id+' search uses player name');
    }
    search.value='';search.dispatchEvent(new Event('input'));
    return '18 old IDs × 5 slots, latest_result, 8 unchanged legacy tests, self/machine tabs, 10 paged DeepTalk rounds, search and XSS';
  } finally { window.fetch=originalFetch; }
}
module.exports = {checkHistoryTitles};
