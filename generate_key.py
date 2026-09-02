import secrets
print('API key: juno_' + secrets.token_urlsafe(32))
print('Admin key: ' + secrets.token_urlsafe(48))
