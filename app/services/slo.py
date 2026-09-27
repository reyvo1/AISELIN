from __future__ import annotations
from app.services.telemetry import counters

def current_slo(error_rate_target:float=0.01,avg_latency_ms_target:float=500.0)->dict:
    c=counters();total=max(0,c.get('http.requests.total',0));errors=sum(v for k,v in c.items() if k.startswith('http.status.5'))
    duration_count=max(0,c.get('http.duration_us.count',0));duration_sum=max(0,c.get('http.duration_us.sum',0))
    error_rate=(errors/total) if total else 0.0;avg_ms=(duration_sum/duration_count/1000.0) if duration_count else 0.0
    return {'ok':error_rate<=error_rate_target and avg_ms<=avg_latency_ms_target,'requests':total,'errors_5xx':errors,'error_rate':error_rate,'error_rate_target':error_rate_target,'avg_latency_ms':avg_ms,'avg_latency_ms_target':avg_latency_ms_target}
