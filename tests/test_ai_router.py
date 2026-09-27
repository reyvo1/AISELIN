from app.ai.router import ProviderConfig, record_usage, usage_for


def test_ai_usage_accounting(client):
    p=ProviderConfig(name='p1',base_url='https://example.invalid',api_key='x',model='m',daily_request_limit=10,daily_token_limit=100)
    record_usage(p,11,7); record_usage(p,3,2)
    u=usage_for(p); assert u['requests']==2 and u['input_tokens']==14 and u['output_tokens']==9


def test_ai_provider_status_endpoint_empty_is_safe(client, auth):
    r=client.get('/api/v1/ai/providers',headers=auth); assert r.status_code==200 and isinstance(r.json(),list)
