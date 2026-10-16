/* Pure JS: paste this fixed reviewed source into the original receiver exec.
 * No imports, filesystem, network, model, native callbacks or persistent state.
 * "observed" must come from the supported native read of the emitted final.
 * The caller pre-pins full expected meanings; child self-labels are not authority.
 */
function receiveFinal(contracts, observed) {
  const limit = 65536;
  const canon = v => {
    if (v === null || typeof v !== "object") return JSON.stringify(v);
    if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
    return "{" + Object.keys(v).sort().map(k => JSON.stringify(k)+":"+canon(v[k])).join(",") + "}";
  };
  const obj = v => v !== null && typeof v === "object" && !Array.isArray(v);
  const keys = (v, a) => obj(v) && canon(Object.keys(v).sort()) === canon(a.slice().sort());
  const txt = v => typeof v === "string" && v.trim() !== "" && Array.from(v).length <= 16000 &&
    !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v);
  const sha = v => typeof v === "string" && v.length === 64 && /^[0-9a-f]{64}$/.test(v);
  const bytes = s => {
    let n=0;
    for (const c of s) { const p=c.codePointAt(0); n+=p<128?1:p<2048?2:p<65536?3:4; }
    return n;
  };
  const valid = e => {
    if (!keys(e,["request","results"]) ||
        !keys(e.request,["sender_thread","target_thread","request_id","payload_utf8_sha256"]) ||
        !["sender_thread","target_thread","request_id"].every(k=>txt(e.request[k])) ||
        !sha(e.request.payload_utf8_sha256) || !Array.isArray(e.results) ||
        e.results.length<1 || e.results.length>16) return false;
    const seen=new Set();
    for(const r of e.results) {
      if(!keys(r,["object_key","report_ref","report_sha256","identities","outcome","summary","limitations","next_scope"]) ||
         !["object_key","report_ref","summary","next_scope"].every(k=>txt(r[k])) ||
         !sha(r.report_sha256) || seen.has(r.object_key) ||
         !["passed","failed","not_run","limited"].includes(r.outcome) ||
         !Array.isArray(r.limitations) || !r.limitations.length || !r.limitations.every(txt) ||
         !Array.isArray(r.identities)) return false;
      seen.add(r.object_key);
      const ids=new Set();
      for(const i of r.identities) {
        if(!keys(i,["name","value"]) || !txt(i.name) || !txt(i.value) || ids.has(i.name)) return false;
        ids.add(i.name);
      }
    }
    return bytes(canon(e))<=limit;
  };
  if (!Array.isArray(contracts) || !contracts.length || contracts.length>32 ||
      !contracts.every(valid) || new Set(contracts.map(e=>e.request.request_id)).size!==contracts.length) {
    return {return_acceptance:"invalid",reason:"expected_contract_invalid",
            accepted_request_refs:[],pending_request_refs:[]};
  }
  const pending=contracts.map(e=>e.request.request_id);
  const answer=(state,reason,accepted=[])=>({
    return_acceptance:state,reason,accepted_request_refs:accepted,
    pending_request_refs:pending.filter(r=>!accepted.includes(r)),
    preserve_completed_work:true,independent_work_may_continue:true,
    native_final_ref:obj(observed)&&txt(observed.final_ref)?observed.final_ref:null
  });
  if (!obj(observed) || !txt(observed.thread_id) || !txt(observed.final_ref) ||
      observed.completed!==true || observed.truncated!==false || typeof observed.text!=="string") {
    return answer("unknown","native_final_incomplete_or_unavailable");
  }
  if (bytes(observed.text)>limit) return answer("unknown","native_final_too_large");
  let actual;
  try { actual=JSON.parse(observed.text); } catch (_) { return answer("rejected","whole_final_format_mismatch"); }
  if (!keys(actual,["format","request","results","ledger_after_sha256"]) ||
      actual.format!=="finite-return-v2" || !sha(actual.ledger_after_sha256) ||
      !valid({request:actual.request,results:actual.results})) {
    return answer("rejected","whole_final_format_mismatch");
  }
  const selected=contracts.find(e=>canon(e.request)===canon(actual.request));
  if (!selected) return answer("rejected","current_known_request_not_returned");
  if (observed.thread_id!==selected.request.target_thread) return answer("rejected","native_source_mismatch");
  const expected=canon({format:"finite-return-v2",request:selected.request,results:selected.results,
                        ledger_after_sha256:actual.ledger_after_sha256});
  if (observed.text!==expected) return answer("rejected","complete_body_mismatch");
  return answer("accepted","finite_current_return_matched",[selected.request.request_id]);
}

// Bounded UTF-8 SHA-256; standard 32-bit operations, no host modules/globals.
function sha256Utf8(text) {
  if (typeof text !== "string") throw new Error("payload must be a string");
  const bytes=[];
  for (const ch of text) {
    const p=ch.codePointAt(0);
    if(p>=0xd800 && p<=0xdfff) throw new Error("payload contains unpaired surrogate");
    if(p<0x80) bytes.push(p);
    else if(p<0x800) bytes.push(0xc0|(p>>>6),0x80|(p&63));
    else if(p<0x10000) bytes.push(0xe0|(p>>>12),0x80|((p>>>6)&63),0x80|(p&63));
    else bytes.push(0xf0|(p>>>18),0x80|((p>>>12)&63),0x80|((p>>>6)&63),0x80|(p&63));
    if(bytes.length>65536) throw new Error("payload exceeds 65536 UTF-8 bytes");
  }
  const bits=bytes.length*8;
  bytes.push(128);
  while(bytes.length%64!==56) bytes.push(0);
  bytes.push(0,0,0,0,(bits>>>24)&255,(bits>>>16)&255,(bits>>>8)&255,bits&255);
  const K=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const H=[0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const ro=(x,n)=>(x>>>n)|(x<<(32-n));
  for(let offset=0;offset<bytes.length;offset+=64) {
    const w=[];
    for(let i=0;i<16;i++){const j=offset+i*4;w[i]=((bytes[j]<<24)|(bytes[j+1]<<16)|(bytes[j+2]<<8)|bytes[j+3])>>>0;}
    for(let i=16;i<64;i++) {
      const a=w[i-15],b=w[i-2];
      w[i]=(w[i-16]+(ro(a,7)^ro(a,18)^(a>>>3))+w[i-7]+(ro(b,17)^ro(b,19)^(b>>>10)))>>>0;
    }
    let [a,b,c,d,e,f,g,h]=H;
    for(let i=0;i<64;i++) {
      const t1=(h+(ro(e,6)^ro(e,11)^ro(e,25))+((e&f)^(~e&g))+K[i]+w[i])>>>0;
      const t2=((ro(a,2)^ro(a,13)^ro(a,22))+((a&b)^(a&c)^(b&c)))>>>0;
      [a,b,c,d,e,f,g,h]=[(t1+t2)>>>0,a,b,c,(d+t1)>>>0,e,f,g];
    }
    [a,b,c,d,e,f,g,h].forEach((v,i)=>{H[i]=(H[i]+v)>>>0;});
  }
  return H.map(x=>x.toString(16).padStart(8,"0")).join("");
}

function makeDispatch(senderThread, targetThread, requestId, payload, results) {
  if(typeof payload!=="string" || !payload.trim() || Array.from(payload).length>16000) throw new Error("logical payload required");
  const request={sender_thread:senderThread,target_thread:targetThread,
                 request_id:requestId,payload_utf8_sha256:sha256Utf8(payload)};
  const expected=JSON.parse(JSON.stringify({request,results}));
  if(receiveFinal([expected],null).return_acceptance==="invalid") throw new Error("invalid finite contract");
  const canonical=v=>v===null||typeof v!=="object"?JSON.stringify(v):Array.isArray(v)?
    "["+v.map(canonical).join(",")+"]":"{"+Object.keys(v).sort().map(k=>JSON.stringify(k)+":"+canonical(v[k])).join(",")+"}";
  const prompt=canonical({format:"finite-dispatch-v1",payload,expected});
  return {expected,prompt,prompt_utf8_sha256:sha256Utf8(prompt)};
}


if (typeof module !== "undefined") module.exports = {receiveFinal, makeDispatch, sha256Utf8};
