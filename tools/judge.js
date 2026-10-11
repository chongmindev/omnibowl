// Answer judging for the single-player page: same rules as server/judge.py (decision
// "answer judging", P1 + S1); server/tests/judge_cases.json holds the cases both pass.
function norm(s){s=s.replace(/<[^>]+>/g,'').toLowerCase().replace(/[’']/g,'');return s.replace(/[^a-z0-9 ]+/g,' ').replace(/\s+/g,' ').trim()}
function judgeRules(j){const r={ignore:new Set(j.ignore_words||[]),free:j.typo_free_below||0,one:j.one_typo_up_to||0,long:j.long_typos||0};
 r.key=s=>norm(s).split(' ').filter(w=>w&&!r.ignore.has(w)).sort().join(' ');
 r.allowed=n=>(n<r.free||!r.one)?0:(n<=r.one?1:r.long);
 r.terms=new Set((j.terms||[]).map(r.key).filter(k=>k));return r}
function distance(a,b){let p2=null,p=[...Array(b.length+1).keys()];
 for(let i=1;i<=a.length;i++){const c=[i];for(let j=1;j<=b.length;j++){c[j]=Math.min(p[j]+1,c[j-1]+1,p[j-1]+(a[i-1]!==b[j-1]?1:0));
  if(i>1&&j>1&&a[i-1]===b[j-2]&&a[i-2]===b[j-1])c[j]=Math.min(c[j],p2[j-2]+1)}p2=p;p=c}return p[b.length]}
function closeTo(g,forms,others,r){let best=null;
 for(const f of forms){const k=r.allowed(f.length);if(k&&Math.abs(f.length-g.length)<=k){const d=distance(g,f);if(d<=k&&(best===null||d<best))best=d}}
 if(best===null)return false;for(const t of others)if(Math.abs(t.length-g.length)<=best&&distance(g,t)<=best)return false;return true}
function judgeAnswer(given,accept,prompt,r,prompted){const g=r.key(given||'');if(!g)return'reject';
 const acc=new Set(accept.map(r.key)),pr=new Set(prompt.map(r.key));
 if(acc.has(g))return'accept';if(!prompted&&pr.has(g))return'prompt';
 const others=[...r.terms].filter(t=>!acc.has(t)&&!pr.has(t));
 if(closeTo(g,acc,others,r))return'accept';if(!prompted&&closeTo(g,pr,others,r))return'prompt';return'reject'}
if(typeof module!=='undefined')module.exports={norm,judgeRules,judgeAnswer,distance};
