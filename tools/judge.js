// Answer judging for the single-player page: same rules as server/judge.py (decision
// "answer judging", P1 + S1, and "answer judging: leniency"); server/tests/judge_cases.json
// holds the cases both pass.
function norm(s){s=s.replace(/<[^>]+>/g,'').toLowerCase().replace(/[’']/g,'');return s.replace(/[^a-z0-9 ]+/g,' ').replace(/\s+/g,' ').trim()}
function stem(w,min){if(w.length<min)return w;
 for(;;){const old=w;
  if(w.endsWith('ies')&&w.length>4)w=w.slice(0,-3)+'y';
  else if(w.endsWith('ing')&&w.length>=6)w=w.slice(0,-3);
  else if((w.endsWith('ed')||w.endsWith('es'))&&w.length>=5)w=w.slice(0,-2);
  else if(w.endsWith('s')&&!/(ss|us|is)$/.test(w)&&w.length>=4)w=w.slice(0,-1);
  if(w.length>=4&&w[w.length-1]===w[w.length-2]&&!'aeiouls'.includes(w[w.length-1]))w=w.slice(0,-1);
  if(w.length>=4&&w.endsWith('e'))w=w.slice(0,-1);
  if(w===old)return w}}
function judgeRules(j){const r={ignore:new Set(j.ignore_words||[]),free:j.typo_free_below||0,one:j.one_typo_up_to||0,long:j.long_typos||0,stemFrom:j.stem_from||0};
 r.stemKey=k=>r.stemFrom?k.split(' ').map(w=>stem(w,r.stemFrom)).sort().join(' '):k;
 r.key=s=>norm(s).split(' ').filter(w=>w&&!r.ignore.has(w)).sort().join(' ');
 r.allowed=n=>(n<r.free||!r.one)?0:(n<=r.one?1:r.long);
 r.terms=new Set((j.terms||[]).map(r.key).filter(k=>k));return r}
function distance(a,b){let p2=null,p=[...Array(b.length+1).keys()];
 for(let i=1;i<=a.length;i++){const c=[i];for(let j=1;j<=b.length;j++){c[j]=Math.min(p[j]+1,c[j-1]+1,p[j-1]+(a[i-1]!==b[j-1]?1:0));
  if(i>1&&j>1&&a[i-1]===b[j-2]&&a[i-2]===b[j-1])c[j]=Math.min(c[j],p2[j-2]+1)}p2=p;p=c}return p[b.length]}
function closeTo(g,forms,others,r){let best=null;
 for(const f of forms){const k=r.allowed(f.length);if(k&&Math.abs(f.length-g.length)<=k){const d=distance(g,f);if(d<=k&&(best===null||d<best))best=d}}
 if(best===null)return false;for(const t of others)if(Math.abs(t.length-g.length)<=best&&distance(g,t)<=best)return false;return true}
function judgeAnswer(given,accept,prompt,r,prompted,previous){
 if(prompted&&previous&&judgeOne((given||'')+' '+previous,accept,prompt,r,true)==='accept')return'accept';
 return judgeOne(given,accept,prompt,r,prompted)}
function judgeOne(given,accept,prompt,r,prompted){const g=r.key(given||'');if(!g)return'reject';
 const acc=new Set(accept.map(r.key)),pr=new Set(prompt.map(r.key));
 if(acc.has(g))return'accept';if(!prompted&&pr.has(g))return'prompt';
 const others=[...r.terms].filter(t=>!acc.has(t)&&!pr.has(t));
 if(!others.includes(g)){const gs=r.stemKey(g); // a different book term is never an inflection of the answer
  if([...acc].some(a=>r.stemKey(a)===gs))return'accept';if(!prompted&&[...pr].some(p=>r.stemKey(p)===gs))return'prompt'}
 if(closeTo(g,acc,others,r))return'accept';if(!prompted&&closeTo(g,pr,others,r))return'prompt';return'reject'}
if(typeof module!=='undefined')module.exports={norm,judgeRules,judgeAnswer,distance,stem};
