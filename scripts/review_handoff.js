/* Unknown-outcome reviews in the ORIGINAL caller's send/read operations.
 * Inject the reviewed sha256Utf8 from return_receipt_receiver.js. No host globals,
 * callbacks registered with the platform, task queue, authority or secret access.
 * All native IO/persistence remain explicit callbacks of the authorised caller.
 */
function createReviewHandoff(sha256Utf8, receiveKnownFinal=null) {
  const MAX = 65536;
  const fail = message => { const e = new Error(message); e.name = "ReviewHandoffError"; throw e; };
  const object = v => v !== null && typeof v === "object" && !Array.isArray(v);
  const text = v => typeof v === "string" && v.trim() !== "" && Array.from(v).length <= 16000 &&
    !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v);
  const sha = v => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
  const need = (ok, message) => { if (!ok) fail(message); };
  const bytes = s => { let n=0; for (const c of s) { const p=c.codePointAt(0); n+=p<128?1:p<2048?2:p<65536?3:4; } return n; };
  const hash = value => {
    need(typeof value==="string" && bytes(value)<=MAX,"hashed text unavailable or too large");
    need(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value),"hashed text invalid unicode");
    return sha256Utf8(value);
  };
  function canon(value) {
    let nodes=0;
    function encode(v, depth) {
      need(++nodes<=12000 && depth<=20, "JSON bounds");
      if(v===null || typeof v==="boolean" || (typeof v==="number" && Number.isFinite(v))) return JSON.stringify(v);
      if(typeof v==="string") { need(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v),"invalid unicode"); return JSON.stringify(v); }
      if(Array.isArray(v)) return "["+Array.from(v,x=>encode(x,depth+1)).join(",")+"]";
      need(object(v), "JSON value");
      return "{"+Object.keys(v).sort().map(k=>JSON.stringify(k)+":"+encode(v[k],depth+1)).join(",")+"}";
    }
    const encoded=encode(value,0); need(bytes(encoded)<=MAX,"JSON too large"); return encoded;
  }
  const same = (a,b) => canon(a)===canon(b);
  const keys = (v,names) => object(v) && same(Object.keys(v).sort(),names.slice().sort());
  function parse(raw, canonical=false, max=MAX) {
    need(typeof raw==="string" && bytes(raw)<=max,"text unavailable or too large");
    let value; try { value=JSON.parse(raw); } catch(e) { if(e instanceof SyntaxError) fail("malformed JSON"); throw e; }
    // JSON.parse discards duplicate object members. Scan the already syntax-
    // checked token stream so pretty original reports need not be canonical.
    const tokens=raw.match(/"(?:[^"\\]|\\.)*"|[{}\[\]:,]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/g)||[];
    let at=0;
    function unique(depth) {
      need(depth<=30,"JSON depth");
      const t=tokens[at++];
      if(t==="{") {
        const seen=new Set();
        while(tokens[at]!=="}") {
          const k=JSON.parse(tokens[at++]); need(!seen.has(k),"duplicate JSON field"); seen.add(k); at++; unique(depth+1);
          if(tokens[at]===",") at++;
        } at++;
      } else if(t==="[") { while(tokens[at]!=="]") { unique(depth+1); if(tokens[at]===",") at++; } at++; }
    }
    unique(0);
    // Roundtrip rejects duplicate fields, trailing text, whitespace and extra prose.
    if(canonical) need(canon(value)===raw,"whole text is not canonical");
    return value;
  }
  function request(r) {
    need(keys(r,["sender_thread","target_thread","request_id","payload_utf8_sha256"]),"request fields");
    need(["sender_thread","target_thread","request_id"].every(k=>text(r[k])) && sha(r.payload_utf8_sha256),"request values");
  }
  function subjects(rows) {
    need(Array.isArray(rows) && rows.length>0 && rows.length<=16,"subjects array");
    const seen=new Set();
    for(const r of rows) {
      need(keys(r,["object_key","ref","sha256"]) && text(r.object_key) && text(r.ref) && sha(r.sha256),"subject fields");
      need(!seen.has(r.object_key),"duplicate subject"); seen.add(r.object_key);
    }
  }
  function contract(c) {
    need(keys(c,["request","subjects","questions","supersedes"]),"contract fields"); request(c.request); subjects(c.subjects);
    need(Array.isArray(c.questions) && c.questions.length>0 && c.questions.length<=64,"questions array");
    const seen=new Set();
    for(const q of c.questions) {
      need(keys(q,["question_key","text","object_keys"]) && text(q.question_key) && text(q.text),"question fields");
      need(!seen.has(q.question_key),"duplicate question"); seen.add(q.question_key);
      need(Array.isArray(q.object_keys) && q.object_keys.length>0 && q.object_keys.length<=16 &&
        q.object_keys.every(k=>text(k) && c.subjects.some(s=>s.object_key===k)) &&
        new Set(q.object_keys).size===q.object_keys.length,"question subjects");
    }
    need(c.questions.reduce((n,q)=>n+q.object_keys.length,0)<=128,"too many obligations");
    if(c.supersedes!==null) {
      request(c.supersedes);
      need(c.supersedes.sender_thread===c.request.sender_thread && c.supersedes.target_thread===c.request.target_thread &&
        c.supersedes.request_id!==c.request.request_id,"revision must name own different request");
    }
    canon(c);
  }
  function parseReviewDispatch(prompt) {
    const p=parse(prompt,true);
    need(keys(p,["format","payload","contract"]) && p.format==="review-dispatch-v1" && text(p.payload),"dispatch fields");
    contract(p.contract); need(hash(p.payload)===p.contract.request.payload_utf8_sha256,"payload fingerprint");
    return p;
  }
  function dispatch(d) {
    need(keys(d,["contract","prompt","prompt_utf8_sha256"]) && sha(d.prompt_utf8_sha256),"dispatch record fields");
    const p=parseReviewDispatch(d.prompt);
    need(same(p.contract,d.contract) && hash(d.prompt)===d.prompt_utf8_sha256,"dispatch record mismatch");
    return p;
  }
  function makeReviewDispatch(sender,target,id,payload,inputSubjects,questions,supersedes=null) {
    need(text(payload),"logical payload required");
    const c={request:{sender_thread:sender,target_thread:target,request_id:id,payload_utf8_sha256:hash(payload)},
      subjects:inputSubjects,questions,supersedes};
    contract(c);
    const prompt=canon({format:"review-dispatch-v1",payload,contract:c});
    return JSON.parse(canon({contract:c,prompt,prompt_utf8_sha256:hash(prompt)}));
  }
  function recoverDispatch(raw,pinnedSha256) {
    if(typeof raw!=="string" || !sha(pinnedSha256)) return {state:"unknown",reason:"original_sender_record_unavailable",may_resend:false};
    try {
      need(hash(raw)===pinnedSha256,"original sender record fingerprint");
      const record=parse(raw,true);
      need(keys(record,["dispatch","attempt","native_receipt"]) && ["prepared","started","returned","unknown"].includes(record.attempt),"sender record fields");
      dispatch(record.dispatch);
      return {state:"recovered",record,may_resend:false,reason:"inspect_original_native_state_before_continuation"};
    } catch(e) { if(e.name!=="ReviewHandoffError") throw e; return {state:"unknown",reason:e.message,may_resend:false}; }
  }
  async function sendReview(record,io) {
    // Caller retains in its existing handoff evidence. No cache lookup or implicit
    // fresh record, deduplication claim, resend, retry, alternate tool or recipient.
    need(object(record) && keys(record,["dispatch","attempt","native_receipt"]),"original sender record required");
    dispatch(record.dispatch);
    need(object(io) && typeof io.retain==="function" && typeof io.send==="function","original send/retain callbacks required");
    if(record.attempt!=="prepared") return {state:"unknown",reason:"attempt_already_started_inspect_original",may_resend:false};
    record.attempt="started";
    need(await io.retain(canon(record))===true,"original sender record not retained; no send");
    let receipt;
    try { receipt=await io.send(record.dispatch.contract.request.target_thread,record.dispatch.prompt); }
    catch(e) {
      record.attempt="unknown"; record.native_receipt=null; await io.retain(canon(record));
      return {state:"unknown",reason:"native_send_threw_inspect_original",may_resend:false};
    }
    record.attempt="returned";
    // Native receipt is saved by the caller separately; no arbitrary host object
    // is canonicalised here, and an acknowledgement never proves execution.
    record.native_receipt={prompt_utf8_sha256:record.dispatch.prompt_utf8_sha256};
    const retained=await io.retain(canon(record))===true;
    return {state:retained?"native_returned":"unknown",reason:"verify_actual_receiver_input",may_resend:false,native_receipt:receipt};
  }
  function nativeBody(raw) {
    need(object(raw) && raw.isError!==true && (raw.isError===undefined || raw.isError===false),"native tool error");
    need(Array.isArray(raw.content) && raw.content.length===1 && object(raw.content[0]) && raw.content[0].type==="text","unsupported native envelope");
    // This is the actual cloud_threads projection, not codex_app's JSON text.
    // Keep its original fields. It currently exposes neither the original input
    // nor a native final-phase identity, so recognising it cannot establish a return.
    const c=raw.structuredContent;
    if(object(c) && Object.prototype.hasOwnProperty.call(c,"threadId")) {
      need(raw.content[0].text==="Action completed." &&
        !["schemaVersion","thread","turns"].some(k=>Object.prototype.hasOwnProperty.call(c,k)),"conflicting cloud native envelope");
      need(text(c.threadId) && object(c.latestTurn) && text(c.latestTurn.id) && text(c.latestTurn.status),"cloud thread/turn shape");
      need(c.parentThreadId===undefined || c.parentThreadId===null || text(c.parentThreadId),"cloud parent shape");
      need(c.nextCursor===undefined || c.nextCursor===null || text(c.nextCursor),"cloud cursor shape");
      need(Array.isArray(c.items) && c.items.length<=4096,"cloud items shape");
      let total=0;
      const charge=values=>{ for(const value of values) total+=bytes(value); need(total<=2*1024*1024,"cloud projection text budget"); };
      charge([c.threadId,c.latestTurn.id,c.latestTurn.status,c.parentThreadId||"",c.nextCursor||""]);
      const ids=new Set();
      for(const i of c.items) {
        need(object(i) && text(i.id) && text(i.turnId) && text(i.role) &&
          typeof i.text==="string" && bytes(i.text)<=2*1024*1024 &&
          typeof i.truncated==="boolean","cloud item shape");
        charge([i.id,i.turnId,i.role,i.text]);
        need(!ids.has(i.id),"duplicate cloud item reference"); ids.add(i.id);
      }
      return {schema:"cloud_threads",body:c};
    }
    const b=parse(raw.content[0].text,false,2*1024*1024);
    if(raw.structuredContent!==undefined) need(same(raw.structuredContent,b),"conflicting native envelope");
    need(object(b) && b.schemaVersion===1 && object(b.thread) && Array.isArray(b.turns),"unsupported native read schema");
    need(!Object.prototype.hasOwnProperty.call(b,"threadId"),"conflicting native read schemas");
    return {schema:"codex_app",body:b};
  }
  function cloudUnknown(b,target,originalTurn=null) {
    need(b.threadId===target,"native thread mismatch");
    return {state:"unknown",native_read_schema:"cloud_threads",
      reason:originalTurn!==null && originalTurn!==b.latestTurn.id ? "cloud historical turn status unavailable" :
        "cloud projection lacks original input and native final identity",
      next_cursor:b.nextCursor===undefined?null:b.nextCursor};
  }
  function inputMessage(item) {
    if(!object(item)) return null;
    const xml=s=>{
      if(typeof s!=="string") return null;
      const m=s.match(/^\s*<codex_delegation>\s*<source_thread_id>([^<]+)<\/source_thread_id>\s*<input>([\s\S]*)<\/input>\s*<\/codex_delegation>\s*$/);
      return m?{sender:m[1],prompt:m[2]}:null;
    };
    if(item.type==="userMessage" && Array.isArray(item.content) && item.content.length===1) {
      const c=item.content[0]; if(!object(c)) return null;
      const found=xml(c.text);
      if(c.type==="text" && object(c.codexDelegation) && found &&
        found.sender===c.codexDelegation.sourceThreadId && found.prompt===c.codexDelegation.input) return found;
    }
    if(item.type==="functionCallOutput" && item.name==="send_message" && item.namespace==="cloud_threads" &&
      object(item.output) && item.output.truncated===false) return xml(item.output.text);
    return null;
  }
  function recoverNativeDispatch(raw,pin) {
    // Pin comes from original sender acknowledgement/history, NOT a child final.
    try {
      need(keys(pin,["sender_thread","target_thread","turn_id","prompt_utf8_sha256"]) &&
        ["sender_thread","target_thread","turn_id"].every(k=>text(pin[k])) && sha(pin.prompt_utf8_sha256),"original send pin unavailable");
      const native=nativeBody(raw), b=native.body;
      if(native.schema==="cloud_threads")
        return {...cloudUnknown(b,pin.target_thread,pin.turn_id),may_resend:false,acceptance:"unreviewed"};
      need(b.thread.id===pin.target_thread,"original target mismatch");
      const turns=b.turns.filter(t=>object(t) && t.id===pin.turn_id); need(turns.length===1 && Array.isArray(turns[0].items),"original turn missing or ambiguous");
      const found=turns[0].items.map(i=>({item:i,input:inputMessage(i)})).filter(x=>x.input &&
        x.input.sender===pin.sender_thread && hash(x.input.prompt)===pin.prompt_utf8_sha256);
      need(found.length===1,"original sent input missing or ambiguous");
      need(text(found[0].item.id),"original native input reference missing");
      const p=parse(found[0].input.prompt,true);
      let recovered;
      if(p.format==="review-dispatch-v1") {
        parseReviewDispatch(found[0].input.prompt);
        recovered={mode:"review",dispatch:{contract:p.contract,prompt:found[0].input.prompt,prompt_utf8_sha256:pin.prompt_utf8_sha256}};
      } else {
        need(keys(p,["format","payload","expected"]) && p.format==="finite-dispatch-v1" && text(p.payload),"unsupported original dispatch");
        need(typeof receiveKnownFinal==="function" && receiveKnownFinal([p.expected],null).return_acceptance!=="invalid","known contract validator required");
        need(hash(p.payload)===p.expected.request.payload_utf8_sha256,"original known payload mismatch");
        recovered={mode:"known",expected:p.expected,prompt:found[0].input.prompt};
      }
      const r=recovered.mode==="known"?recovered.expected.request:recovered.dispatch.contract.request;
      need(r.sender_thread===pin.sender_thread && r.target_thread===pin.target_thread,"original request source/target mismatch");
      return {state:"recovered",...recovered,native_input_ref:found[0].item.id,native_turn_id:pin.turn_id,
        may_resend:false,acceptance:"unreviewed",pending_set:"only_this_pinned_original_request_recovered"};
    } catch(e) { if(e.name!=="ReviewHandoffError") throw e; return {state:"unknown",reason:e.message,may_resend:false,acceptance:"unreviewed"}; }
  }
  function delegated(item,d) {
    if(!object(item)) return false;
    const req=d.contract.request;
    const xml = s => {
      if(typeof s!=="string") return false;
      const m=s.match(/^\s*<codex_delegation>\s*<source_thread_id>([^<]+)<\/source_thread_id>\s*<input>([\s\S]*)<\/input>\s*<\/codex_delegation>\s*$/);
      return !!m && m[1]===req.sender_thread && m[2]===d.prompt;
    };
    if(item.type==="userMessage" && Array.isArray(item.content)) {
      return item.content.length===1 && object(item.content[0]) && item.content[0].type==="text" &&
        object(item.content[0].codexDelegation) && item.content[0].codexDelegation.sourceThreadId===req.sender_thread &&
        item.content[0].codexDelegation.input===d.prompt && xml(item.content[0].text);
    }
    return item.type==="functionCallOutput" && item.name==="send_message" && item.namespace==="cloud_threads" &&
      object(item.output) && item.output.truncated===false && xml(item.output.text);
  }
  function observeReview(d,raw) {
    try {
      dispatch(d); const native=nativeBody(raw), b=native.body;
      if(native.schema==="cloud_threads") return cloudUnknown(b,d.contract.request.target_thread);
      need(b.thread.id===d.contract.request.target_thread,"native thread mismatch");
      const matches=b.turns.filter(t=>object(t) && Array.isArray(t.items) && t.items.some(i=>delegated(i,d)));
      need(matches.length===1,"native request turn missing or ambiguous");
      const t=matches[0], inputs=t.items.filter(i=>delegated(i,d));
      need(inputs.length===1 && text(inputs[0].id) && text(t.id),"native input missing or ambiguous");
      const finals=t.items.filter(i=>object(i) && i.type==="agentMessage" && i.phase==="final_answer");
      need(t.status==="completed" && (t.error===null || t.error===undefined) && finals.length===1,"native final not uniquely complete");
      const f=finals[0];
      need(text(f.id) && typeof f.text==="string" && (f.truncated===undefined || f.truncated===false) && bytes(f.text)<=MAX,"native final unavailable or truncated");
      return {state:"observed",thread_id:b.thread.id,turn_id:t.id,input_ref:inputs[0].id,final_ref:f.id,
        text:f.text,final_utf8_sha256:hash(f.text)};
    } catch(e) { if(e.name!=="ReviewHandoffError") throw e; return {state:"unknown",reason:e.message}; }
  }
  function pointer(value,path) {
    need(typeof path==="string" && path.startsWith("/") && path.length<=1024,"invalid JSON pointer");
    for(const raw of path.slice(1).split("/")) {
      need(!/~(?![01])/.test(raw),"invalid pointer escape");
      const k=raw.replace(/~1/g,"/").replace(/~0/g,"~");
      need((object(value) && Object.prototype.hasOwnProperty.call(value,k)) ||
        (Array.isArray(value) && /^(0|[1-9][0-9]*)$/.test(k) && Number(k)<value.length),"missing report field");
      value=value[k];
    }
    return value;
  }
  function result(r) {
    need(keys(r,["subjects","answers","limitations","next_scope"]),"result fields"); subjects(r.subjects);
    need(Array.isArray(r.answers) && r.answers.length<=128,"answers array");
    const seen=new Set();
    for(const a of r.answers) {
      need(keys(a,["object_key","question_key","status","answer","evidence"]) &&
        text(a.object_key) && text(a.question_key) && text(a.answer),"answer fields");
      need(["completed","failed","limited","blocked","not_run","unknown"].includes(a.status),"answer status");
      const key=canon([a.object_key,a.question_key]); need(!seen.has(key),"duplicate answer"); seen.add(key);
      need(Array.isArray(a.evidence) && a.evidence.length<=16,"answer evidence array");
      for(const e of a.evidence) need(keys(e,["ref","sha256"]) && text(e.ref) && sha(e.sha256),"answer evidence fields");
      if(a.status==="completed" || a.status==="failed") need(a.evidence.length>0,"completed answer lacks evidence");
    }
    need(Array.isArray(r.limitations) && r.limitations.length>0 && r.limitations.length<=32 && r.limitations.every(text) && text(r.next_scope),"limitations/next scope required");
  }
  function packet(body) {
    need(keys(body,["format","request","report","result"]) && body.format==="review-return-v1","return fields");
    request(body.request); result(body.result);
    need(keys(body.report,["ref","sha256","request_pointer","result_pointer"]) && text(body.report.ref) && sha(body.report.sha256),"report fields");
    for(const k of ["request_pointer","result_pointer"]) need(typeof body.report[k]==="string" && body.report[k].startsWith("/"),"report pointers");
  }
  function receiveReview(dispatches,selectedRequest,raw,readback) {
    let chosen, pending=[];
    const answer=(binding,reason,extra={})=>({binding,reason,coverage:"unknown",result_acceptance:"unreviewed",
      accepted_requests:[],pending_requests:pending,preserve_completed_work:true,independent_work_may_continue:true,...extra});
    try {
      need(Array.isArray(dispatches) && dispatches.length>0 && dispatches.length<=32,"original sender contracts unavailable");
      dispatches.forEach(dispatch); request(selectedRequest);
      const requests=dispatches.map(d=>canon(d.contract.request));
      need(new Set(requests).size===requests.length,"duplicate original request");
      // A sender's request ID cannot be reused for a different payload.
      need(new Set(dispatches.map(d=>canon([d.contract.request.sender_thread,d.contract.request.request_id]))).size===dispatches.length,"reused sender request ID");
      pending=dispatches.map(d=>d.contract.request);
      chosen=dispatches.find(d=>same(d.contract.request,selectedRequest)); need(chosen,"selected original request unavailable");
    } catch(e) { if(e.name!=="ReviewHandoffError") throw e; return answer("unknown",e.message); }
    const observed=observeReview(chosen,raw);
    if(observed.state!=="observed") return answer("unknown",observed.reason,
      observed.native_read_schema==="cloud_threads" ? {native_read_schema:observed.native_read_schema,next_cursor:observed.next_cursor} : {});
    const supersedes=chosen.contract.supersedes;
    const retired=supersedes && dispatches.some(d=>same(d.contract.request,supersedes)) ? [supersedes] : [];
    pending=pending.filter(r=>!retired.some(s=>same(s,r)));
    try {
      const body=parse(observed.text,true); packet(body);
      need(same(body.request,selectedRequest),"current request not returned");
      need(same(body.result.subjects,chosen.contract.subjects),"report input subjects mismatch");
      if(readback===null || readback===undefined || (object(readback) && readback.truncated===true))
        return answer("unknown","original report readback unavailable or truncated",{native_final_ref:observed.final_ref});
      need(object(readback) && keys(readback,["ref","text","truncated","evidence"]) && readback.truncated===false &&
        readback.ref===body.report.ref && typeof readback.text==="string","original report readback unavailable");
      need(hash(readback.text)===body.report.sha256,"report readback fingerprint mismatch");
      const report=parse(readback.text);
      need(same(pointer(report,body.report.request_pointer),body.request),"report original request mismatch");
      need(same(pointer(report,body.report.result_pointer),body.result),"report original result mismatch");
      need(Array.isArray(readback.evidence) && readback.evidence.length<=128,"evidence readbacks unavailable");
      const evidenceKeys=new Set();
      for(const e of readback.evidence) {
        need(keys(e,["ref","text","truncated"]) && text(e.ref) && e.truncated===false && typeof e.text==="string","evidence readback fields");
        need(!evidenceKeys.has(e.ref),"duplicate evidence readback"); evidenceKeys.add(e.ref);
      }
      for(const a of body.result.answers) for(const e of a.evidence) {
        const rb=readback.evidence.find(x=>x.ref===e.ref);
        need(rb && hash(rb.text)===e.sha256,"evidence reference/fingerprint mismatch");
      }
      const obligations=chosen.contract.questions.flatMap(q=>q.object_keys.map(k=>canon([k,q.question_key])));
      need(body.result.answers.every(a=>obligations.includes(canon([a.object_key,a.question_key]))),"unrequested answer object/question");
      const missing=obligations.filter(k=>!body.result.answers.some(a=>canon([a.object_key,a.question_key])===k));
      const incomplete=body.result.answers.filter(a=>!["completed","failed"].includes(a.status));
      return answer("bound","current_input_report_and_whole_final_bound",{
        coverage:missing.length || incomplete.length?"partial":"complete",missing_obligations:missing.map(x=>JSON.parse(x)),
        incomplete_answers:incomplete.map(a=>({object_key:a.object_key,question_key:a.question_key,status:a.status})),
        superseded_requests:retired,request:selectedRequest,native_final_ref:observed.final_ref,
        native_turn_id:observed.turn_id,native_input_ref:observed.input_ref,final_utf8_sha256:observed.final_utf8_sha256,
        report_sha256:body.report.sha256,result:body.result});
    } catch(e) { if(e.name!=="ReviewHandoffError") throw e; return answer("rejected",e.message,{native_final_ref:observed.final_ref}); }
  }
  async function readReview(dispatches,selectedRequest,io) {
    need(object(io) && typeof io.readNative==="function" && typeof io.readReport==="function","original read callbacks required");
    let chosen;
    try { request(selectedRequest); need(Array.isArray(dispatches),"original sender contracts unavailable"); dispatches.forEach(dispatch);
      chosen=dispatches.find(d=>same(d.contract.request,selectedRequest));
    } catch(e) { if(e.name!=="ReviewHandoffError") throw e; return receiveReview(dispatches,selectedRequest,null,null); }
    if(!chosen) return receiveReview(dispatches,selectedRequest,null,null);
    dispatch(chosen);
    let raw;
    try { raw=await io.readNative(selectedRequest.target_thread); }
    catch(e) { return {evaluation:{...receiveReview(dispatches,selectedRequest,null,null),reason:"original native read threw; inspect original operation"},native_readback:null}; }
    const observed=observeReview(chosen,raw);
    let report=null;
    if(observed.state==="observed") {
      try {
        const body=parse(observed.text,true); packet(body);
        // Validate identity before invoking the caller's bounded/authorised reader.
        if(same(body.request,selectedRequest) && same(body.result.subjects,chosen.contract.subjects)) {
          try { report=await io.readReport(body.report.ref); } catch(e) { report=null; }
        }
      } catch(e) { if(e.name!=="ReviewHandoffError") throw e; }
    }
    return {evaluation:receiveReview(dispatches,selectedRequest,raw,report),native_readback:raw};
  }
  function decideReview(bound,decision) {
    need(object(bound) && bound.binding==="bound","bound result required");
    request(bound.request);
    need(Array.isArray(bound.pending_requests) && bound.pending_requests.length<=32,"bound pending requests required");
    bound.pending_requests.forEach(request);
    need(keys(decision,["decision","final_ref","final_utf8_sha256","report_sha256","reason"]) &&
      ["accepted","rejected","unreviewed"].includes(decision.decision) && text(decision.reason),"substantive decision fields");
    need(decision.final_ref===bound.native_final_ref && decision.final_utf8_sha256===bound.final_utf8_sha256 &&
      decision.report_sha256===bound.report_sha256,"decision evidence mismatch");
    need(decision.decision!=="accepted" || bound.coverage==="complete","partial assessment cannot complete request");
    const accepted=decision.decision==="accepted";
    return {...bound,result_acceptance:decision.decision,substantive_reason:decision.reason,
      accepted_requests:accepted?[bound.request]:[],
      pending_requests:accepted?bound.pending_requests.filter(r=>!same(r,bound.request)):bound.pending_requests};
  }
  return {makeReviewDispatch,parseReviewDispatch,sendReview,recoverDispatch,recoverNativeDispatch,observeReview,receiveReview,readReview,decideReview};
}
if(typeof module!=="undefined") module.exports={createReviewHandoff};
