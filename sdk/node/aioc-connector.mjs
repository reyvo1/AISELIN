import crypto from 'node:crypto';

export class AiocConnectorRuntime {
  constructor(applicationId) { this.applicationId = applicationId; this.handlers = new Map(); this.verifiers = new Map(); this.rollbacks = new Map(); }
  capability(name, handler, {verifier=null, rollback=null}={}) {
    if (!name || this.handlers.has(name)) throw new Error('capability name must be unique');
    this.handlers.set(name, handler); if (verifier) this.verifiers.set(name, verifier); if (rollback) this.rollbacks.set(name, rollback);
  }
  capabilities() { return {ok:true, application_id:this.applicationId, capabilities:[...this.handlers.keys()].sort()}; }
  async execute(capability, parameters={}, dryRun=false) {
    const handler=this.handlers.get(capability); if(!handler) throw new Error(`unsupported capability: ${capability}`);
    const value=await handler(parameters,dryRun); if(!value || typeof value!=='object' || Array.isArray(value)) throw new Error('connector handlers must return objects'); return value;
  }
  async verify(capability,payload={}) { const h=this.verifiers.get(capability); return h ? await h(payload,false) : {ok:true,mode:'default'}; }
  async rollback(capability,payload={}) { const h=this.rollbacks.get(capability); return h ? await h(payload,false) : {ok:false,supported:false}; }
}

export function signEvent(secret,event,timestamp=Math.floor(Date.now()/1000)) {
  const body=JSON.stringify(event); const ts=String(timestamp); const signature=crypto.createHmac('sha256',secret).update(`${ts}.${body}`).digest('hex');
  return {body,headers:{'Content-Type':'application/json','X-AIOC-Timestamp':ts,'X-AIOC-Signature':`sha256=${signature}`}};
}
