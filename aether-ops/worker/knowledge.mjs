const stop = new Set('the and for with from this that have what how using use source sources knowledge fabric sop sops rca incident problem problems please get find'.split(' '));
export const knowledgeInputSchema = {type:'object',properties:{query:{type:'string'},approvedOnly:{type:'boolean'}},required:['query'],additionalProperties:false};

// A shared bounded keyword retriever. Source revisions are retained for auditability.
export function searchKnowledge(documents, query, {approvedOnly=false,limit=4}={}) {
 const words=[...new Set(String(query).toLowerCase().match(/[\p{L}\p{N}]{3,}/gu)||[])].filter(w=>!stop.has(w)).slice(0,80);
 return documents.filter(d=>!approvedOnly||d.status==='approved').map(d=>{
  const title=(d.title+' '+d.tags).toLowerCase(),content=d.content.toLowerCase();
  return {d,score:words.reduce((n,w)=>n+(title.includes(w)?3:content.includes(w)?1:0),0)};
 }).filter(x=>x.score>0).sort((a,b)=>b.score-a.score||a.d.id.localeCompare(b.d.id)).slice(0,limit).map(({d})=>({
  id:d.id,title:d.title,revision:d.revision,status:d.status||'unreviewed',updatedAt:d.updatedAt,content:d.content.slice(0,3000),excerptTruncated:d.content.length>3000
 }));
}
